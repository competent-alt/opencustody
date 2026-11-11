"""The bridge between the HTML interface and the wallet.

The page can only call the public methods of `WalletApi`. It never sees the
seed or private keys, and it has no network access of its own (its CSP sets
connect-src 'none'). All networking and signing happens here in Python.

Every method returns a JSON-safe dict: {"ok": True, ...} or {"ok": False, "error": "..."}.
"""

from __future__ import annotations

import os
import secrets
import subprocess  # nosec B404 - only used to open our own config file
import sys
import threading
import time
import webbrowser
from typing import Callable

from . import __version__, config, vault
from .amounts import fmt, to_base_units
from .chains import build_chains, build_monero
from .chains.base import Asset, Chain, PreparedTx
from .keys import Keyring, is_valid_mnemonic, new_mnemonic, normalize_mnemonic
from .paths import config_path, vault_path

_rng = secrets.SystemRandom()


def _ok(**kw) -> dict:
    return {"ok": True, **kw}


def _err(e: BaseException | str) -> dict:
    return {"ok": False, "error": str(e) or e.__class__.__name__}


def _safe(fn: Callable) -> Callable:
    def wrapper(*a, **kw):
        try:
            return fn(*a, **kw)
        except Exception as e:  # surfaced to the user as a message
            return _err(e)
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


class WalletApi:
    def __init__(self):
        config.ensure_file()
        self._lock = threading.RLock()
        self._cfg = config.load()
        self._keyring: Keyring | None = None
        self._chains: list[Chain] = []
        self._accounts: dict[str, tuple[Chain, Asset]] = {}
        self._pending_mnemonic: str | None = None
        self._pending_new = False
        self._verify_positions: list[int] = []
        self._prepared: dict[str, tuple[Chain, PreparedTx]] = {}
        self._last_activity = time.monotonic()
        self._session = 0
        self._monero_state = "off"
        self._monero_error = ""
        self.on_locked: Callable[[], None] | None = None
        threading.Thread(target=self._autolock_loop, daemon=True).start()

    # ---- state ----------------------------------------------------------------------
    @_safe
    def state(self) -> dict:
        return _ok(version=__version__, vault_exists=vault_path().exists(),
                   unlocked=self._keyring is not None)

    @_safe
    def ping(self) -> dict:
        self._last_activity = time.monotonic()
        return _ok()

    def _autolock_loop(self):
        while True:
            time.sleep(5)
            mins = float(self._cfg.get("auto_lock_minutes", 10) or 0)
            if self._keyring and mins > 0 and time.monotonic() - self._last_activity > mins * 60:
                self.lock()
                if self.on_locked:
                    self.on_locked()

    # ---- onboarding -----------------------------------------------------------------
    @_safe
    def create_mnemonic(self) -> dict:
        self._pending_mnemonic = new_mnemonic()
        self._pending_new = True
        return _ok(words=self._pending_mnemonic.split())

    @_safe
    def verify_challenge(self) -> dict:
        if not self._pending_mnemonic:
            return _err("No recovery phrase in progress.")
        n = len(self._pending_mnemonic.split())
        self._verify_positions = sorted(_rng.sample(range(n), 3))
        return _ok(positions=[p + 1 for p in self._verify_positions])

    @_safe
    def verify_words(self, answers: list) -> dict:
        words = (self._pending_mnemonic or "").split()
        if not words or len(answers) != len(self._verify_positions):
            return _err("Start again.")
        good = all(str(a).strip().lower() == words[p] for p, a in zip(self._verify_positions, answers))
        return _ok() if good else _err("Those words don't match your phrase. Check your paper backup.")

    @_safe
    def restore_mnemonic(self, text: str) -> dict:
        m = normalize_mnemonic(text)
        if not is_valid_mnemonic(m):
            return _err("That is not a valid BIP-39 phrase. Check spelling and word order.")
        self._pending_mnemonic = m
        self._pending_new = False
        return _ok()

    @_safe
    def save_wallet(self, password: str, password2: str, passphrase: str = "") -> dict:  # nosec B107
        if not self._pending_mnemonic:
            return _err("No recovery phrase in progress.")
        if password != password2:
            return _err("Passwords don't match.")
        vault.check_password_strength(password)
        s = vault.Secrets(self._pending_mnemonic, passphrase or "")
        vault.save(vault_path(), s, password, {"created_new": self._pending_new, "created_at": int(time.time())})
        self._pending_mnemonic = None
        self._open(s)
        return _ok()

    @_safe
    def unlock(self, password: str) -> dict:
        s = vault.load(vault_path(), password)
        self._open(s)
        return _ok()

    def _open(self, s: vault.Secrets) -> None:
        with self._lock:
            self._cfg = config.load()
            self._keyring = Keyring(s.mnemonic, s.passphrase)
            self._chains, errors = build_chains(self._cfg, self._keyring)
            self._index_accounts()
            self._build_errors = errors
            self._monero_state = "off" if not self._cfg["monero"].get("enabled") else "idle"
            self._last_activity = time.monotonic()

    def _index_accounts(self) -> None:
        self._accounts = {}
        for ci, chain in enumerate(self._chains):
            for ai, asset in enumerate(chain.assets()):
                self._accounts[f"{chain.key}:{ai}" if chain.key else f"{ci}:{ai}"] = (chain, asset)

    @_safe
    def lock(self) -> dict:
        with self._lock:
            self._session += 1
            chains, self._chains = self._chains, []
            self._accounts = {}
            self._prepared.clear()
            self._pending_mnemonic = None
            for c in chains:
                try:
                    c.close()
                except Exception:  # nosec B110 - best-effort shutdown
                    pass
            if self._keyring:
                self._keyring.wipe()
            self._keyring = None
        return _ok()

    # ---- accounts -------------------------------------------------------------------
    @_safe
    def accounts(self) -> dict:
        if not self._keyring:
            return _err("Wallet is locked.")
        out = []
        for aid, (chain, asset) in self._accounts.items():
            out.append({
                "id": aid, "network": chain.name, "symbol": asset.symbol, "native": chain.native.symbol,
                "token": asset.contract, "address": chain.address(),
                "max": bool(chain.supports_max and asset.contract is None),
            })
        notes = list(getattr(self, "_build_errors", []))
        return _ok(accounts=out, notes=notes, monero=self._monero_state, monero_error=self._monero_error)

    @_safe
    def balance(self, aid: str) -> dict:
        chain, asset = self._get(aid)
        units = chain.balance(asset)
        return _ok(balance=fmt(units, asset.decimals))

    def _get(self, aid: str) -> tuple[Chain, Asset]:
        if not self._keyring:
            raise RuntimeError("Wallet is locked.")
        if aid not in self._accounts:
            raise KeyError("Unknown account.")
        return self._accounts[aid]

    @_safe
    def start_monero(self) -> dict:
        if not self._keyring:
            return _err("Wallet is locked.")
        if not self._cfg["monero"].get("enabled"):
            return _err("Monero is off. Enable it in Settings.")
        if self._monero_state in ("starting", "on"):
            return _ok()
        self._monero_state = "starting"
        session = self._session
        try:
            meta = vault.read_metadata(vault_path())
            height = int(self._cfg["monero"].get("restore_height") or 0)
            if meta.get("created_new") and not height:
                from .chains.monero import daemon_height
                tip = daemon_height(self._cfg["monero"]["daemon"])
                height = max(0, tip - (int(time.time()) - int(meta.get("created_at", 0))) // 120 - 2000)
            chain = build_monero(self._cfg, self._keyring, height)
        except Exception as e:
            self._monero_state, self._monero_error = "error", str(e)
            return _err(e)
        with self._lock:
            if session != self._session:  # locked while starting
                chain.close()
                return _err("Wallet was locked.")
            self._chains.append(chain)
            self._index_accounts()
            self._monero_state, self._monero_error = "on", ""
        return _ok()

    # ---- sending --------------------------------------------------------------------
    @_safe
    def validate(self, aid: str, to: str) -> dict:
        chain, _ = self._get(aid)
        e = chain.validate_address(to.strip())
        return _ok(valid=e is None, error=e or "")

    @_safe
    def prepare_send(self, aid: str, to: str, amount: str, send_max: bool = False) -> dict:
        chain, asset = self._get(aid)
        units = None if send_max else to_base_units(str(amount), asset.decimals)
        if send_max and not chain.supports_max:
            return _err("Send-all is not available on this network.")
        ptx = chain.prepare_send(asset, to.strip(), units)
        token = secrets.token_urlsafe(16)
        self._prepared = {token: (chain, ptx)}  # only the latest review can be sent
        rows = [
            ["Network", ptx.chain], ["From", ptx.sender], ["To", ptx.recipient],
            ["Amount", f"{fmt(ptx.amount, asset.decimals)} {asset.symbol}"],
            ["Network fee", f"{fmt(ptx.fee, ptx.native.decimals)} {ptx.native.symbol} {ptx.fee_note}".strip()],
        ]
        if asset.contract is None:
            rows.append(["Total", f"{fmt(ptx.amount + ptx.fee, ptx.native.decimals)} {ptx.native.symbol}"])
        else:
            rows.append(["Token contract", asset.contract])
        return _ok(token=token, rows=rows, warnings=ptx.warnings)

    @_safe
    def broadcast(self, token: str) -> dict:
        item = self._prepared.pop(token, None)
        if item is None:
            return _err("This review expired. Review the transaction again.")
        chain, ptx = item
        txid = ptx.broadcast()
        return _ok(txid=txid, url=chain.tx_url(txid))

    @_safe
    def cancel_send(self) -> dict:
        self._prepared.clear()
        return _ok()

    # ---- misc -----------------------------------------------------------------------
    @_safe
    def explorer(self, aid: str) -> dict:
        chain, _ = self._get(aid)
        return self.open_url(chain.address_url())

    @_safe
    def open_url(self, url: str) -> dict:
        if not str(url).startswith("https://"):
            return _err("Only https links can be opened.")
        webbrowser.open(url)
        return _ok()

    @_safe
    def settings(self) -> dict:
        m = self._cfg["monero"]
        return _ok(config_path=str(config_path()), vault_path=str(vault_path()),
                   auto_lock=self._cfg.get("auto_lock_minutes", 10),
                   monero_enabled=bool(m.get("enabled")), monero_rpc=m.get("wallet_rpc_path", ""),
                   monero_daemon=m.get("daemon", ""), monero_state=self._monero_state,
                   monero_error=self._monero_error)

    @_safe
    def open_config(self) -> dict:
        p = str(config_path())
        if sys.platform == "win32":
            os.startfile(p)  # nosec B606 - our own config file
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            subprocess.Popen([opener, p])  # nosec B603 B607 - fixed command, our own file
        return _ok()
