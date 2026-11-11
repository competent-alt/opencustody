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

from . import __version__, config, net, portfolios, prices, vault
from .amounts import fmt, to_base_units
from .chains import build_chains, build_monero
from .chains.base import Asset, Chain, HistoryUnavailable, PreparedTx
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
        self._portfolio = 0
        self._build_errors: list[str] = []
        self._on_locked: Callable[[], None] | None = None
        self._window = None  # set by gui.py, used for the file picker
        self._apply_network()
        threading.Thread(target=self._autolock_loop, daemon=True).start()

    # ---- privacy plumbing -----------------------------------------------------------
    def _apply_network(self) -> None:
        t = self._cfg.get("tor", {})
        net.configure(t.get("proxy") if t.get("enabled") else None)

    def _privacy_mode(self) -> bool:
        return bool(self._cfg.get("privacy", {}).get("mode"))

    def _circ(self):
        """Each portfolio talks to the network over its own Tor circuit (separate exit IP)."""
        return net.circuit(f"portfolio-{self._portfolio}")

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
                if self._on_locked:
                    self._on_locked()

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
            self._last_activity = time.monotonic()
            self._load_portfolio(portfolios.load()["active"])

    def _close_chains(self) -> None:
        chains, self._chains = self._chains, []
        self._accounts = {}
        self._prepared.clear()
        for c in chains:
            try:
                c.close()
            except Exception:  # nosec B110 - best-effort shutdown
                pass

    def _load_portfolio(self, n: int) -> None:
        self._session += 1
        self._close_chains()
        self._portfolio = n
        self._chains, self._build_errors = build_chains(self._cfg, self._keys())
        self._index_accounts()
        self._monero_state = "off" if not self._cfg["monero"].get("enabled") else "idle"
        self._monero_error = ""

    def _keys(self):
        if not self._keyring:
            raise RuntimeError("Wallet is locked.")
        return self._keyring.portfolio(self._portfolio)

    # ---- portfolios -----------------------------------------------------------------
    @_safe
    def portfolios(self) -> dict:
        d = portfolios.load()
        return _ok(active=self._portfolio if self._keyring else d["active"], items=d["items"])

    @_safe
    def switch_portfolio(self, n: int) -> dict:
        n = int(n)
        d = portfolios.load()
        if n not in {i["n"] for i in d["items"]}:
            return _err("Unknown portfolio.")
        with self._lock:
            self._keys()
            self._load_portfolio(n)
            d["active"] = n
            portfolios.save(d)
        return _ok()

    @_safe
    def create_portfolio(self, name: str) -> dict:
        self._keys()
        d = portfolios.load()
        n = portfolios.add(d, name)
        portfolios.save(d)
        self.switch_portfolio(n)
        return _ok(n=n)

    @_safe
    def rename_portfolio(self, n: int, name: str) -> dict:
        d = portfolios.load()
        for i in d["items"]:
            if i["n"] == int(n):
                i["name"] = portfolios.clean_name(name)
                portfolios.save(d)
                return _ok()
        return _err("Unknown portfolio.")

    @_safe
    def remove_portfolio(self, n: int) -> dict:
        """Hides a portfolio from the list. Its funds stay on-chain and it can be re-added."""
        n = int(n)
        if n == 0:
            return _err("The main portfolio can't be removed.")
        d = portfolios.load()
        d["items"] = [i for i in d["items"] if i["n"] != n]
        if d["active"] == n:
            d["active"] = 0
        portfolios.save(d)
        if self._keyring and self._portfolio == n:
            with self._lock:
                self._load_portfolio(0)
        return _ok()

    # ---- prices & history -----------------------------------------------------------
    @_safe
    def prices(self) -> dict:
        p = self._cfg.get("prices", {})
        if not p.get("enabled", True) or self._privacy_mode():
            return _ok(enabled=False, prices={}, privacy=self._privacy_mode())
        syms = sorted({a.symbol for _, a in self._accounts.values()} | {"XMR"})
        with net.circuit("prices"):  # never shares a circuit with address lookups
            data = prices.fetch(p.get("api", "https://api.coingecko.com/api/v3"), syms)
        return _ok(enabled=True, prices=data, updated=int(time.time()))

    @_safe
    def history(self, aid: str) -> dict:
        chain, asset = self._get(aid)
        if self._privacy_mode() and chain.key != "xmr":
            return _ok(items=[], unavailable="Privacy mode is on: third-party history lookups are off.")
        try:
            with self._circ():
                items = chain.history(asset)
        except HistoryUnavailable as e:
            return _ok(items=[], unavailable=str(e))
        return _ok(items=[{
            "id": aid, "txid": i.txid, "time": i.time, "dir": i.direction,
            "amount": fmt(i.amount, asset.decimals), "symbol": asset.symbol, "network": chain.name,
            "counterparty": i.counterparty, "status": i.status, "url": chain.tx_url(i.txid),
        } for i in items])

    def _index_accounts(self) -> None:
        self._accounts = {}
        for ci, chain in enumerate(self._chains):
            for ai, asset in enumerate(chain.assets()):
                self._accounts[f"{chain.key}:{ai}" if chain.key else f"{ci}:{ai}"] = (chain, asset)

    @_safe
    def lock(self) -> dict:
        with self._lock:
            self._session += 1
            self._close_chains()
            self._pending_mnemonic = None
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
                "coin_control": bool(getattr(chain, "supports_coin_control", False) and asset.contract is None),
            })
        notes = list(getattr(self, "_build_errors", []))
        return _ok(accounts=out, notes=notes, monero=self._monero_state, monero_error=self._monero_error,
                   portfolio=self._portfolio)

    @_safe
    def balance(self, aid: str) -> dict:
        chain, asset = self._get(aid)
        with self._circ():
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
                with self._circ():
                    tip = daemon_height(self._cfg["monero"]["daemon"])
                height = max(0, tip - (int(time.time()) - int(meta.get("created_at", 0))) // 120 - 2000)
            chain = build_monero(self._cfg, self._keys(), height)
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
        with self._circ():
            e = chain.validate_address(to.strip())
        return _ok(valid=e is None, error=e or "")

    @_safe
    def coins(self, aid: str) -> dict:
        """Spendable coins for coin control (Bitcoin / Litecoin)."""
        chain, asset = self._get(aid)
        if not getattr(chain, "supports_coin_control", False) or asset.contract:
            return _err("Coin control is only for Bitcoin and Litecoin.")
        with self._circ():
            items = chain.coins(asset)
        for c in items:
            c["amount"] = fmt(c.pop("value"), asset.decimals)
        return _ok(coins=items, symbol=asset.symbol)

    @_safe
    def prepare_send(self, aid: str, to: str, amount: str, send_max: bool = False, coins: list | None = None) -> dict:
        chain, asset = self._get(aid)
        units = None if send_max else to_base_units(str(amount), asset.decimals)
        if send_max and not chain.supports_max:
            return _err("Send-all is not available on this network.")
        extra = {}
        if coins:
            if not getattr(chain, "supports_coin_control", False):
                return _err("Coin control is only for Bitcoin and Litecoin.")
            extra["coins"] = [str(c) for c in coins]
        with self._circ():
            ptx = chain.prepare_send(asset, to.strip(), units, **extra)
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
        with self._circ():
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
        if self._privacy_mode():
            # Your everyday browser would reveal the address/transaction with your real IP.
            return _ok(copy=url)
        webbrowser.open(url)
        return _ok()

    # ---- privacy settings -----------------------------------------------------------
    @_safe
    def privacy_status(self) -> dict:
        t, p, m = self._cfg.get("tor", {}), self._cfg.get("privacy", {}), self._cfg["monero"]
        defaults = config.DEFAULTS
        endpoints = [
            ("Bitcoin", self._cfg["bitcoin"]["esplora"], defaults["bitcoin"]["esplora"]),
            ("Litecoin", self._cfg["litecoin"]["esplora"], defaults["litecoin"]["esplora"]),
            *[(e["name"], e["rpc"], next((d["rpc"] for d in defaults["evm"] if d["key"] == e["key"]), ""))
              for e in self._cfg["evm"]],
            ("Solana", self._cfg["solana"]["rpc"], defaults["solana"]["rpc"]),
            ("Tron", self._cfg["tron"]["api"], defaults["tron"]["api"]),
            ("TON", self._cfg["ton"]["api"], defaults["ton"]["api"]),
        ]
        from urllib.parse import urlparse
        eps = [{"network": n, "host": urlparse(u).hostname or u, "own": u != d} for n, u, d in endpoints]
        return _ok(tor_enabled=bool(t.get("enabled")), tor_proxy=t.get("proxy", "127.0.0.1:9050"),
                   mode=bool(p.get("mode")), hide_balances=bool(p.get("hide_balances")),
                   prices_enabled=bool(self._cfg.get("prices", {}).get("enabled", True)) and not p.get("mode"),
                   endpoints=eps, monero_enabled=bool(m.get("enabled")), monero_state=self._monero_state,
                   monero_rpc=m.get("wallet_rpc_path", ""), monero_daemon=m.get("daemon", ""),
                   monero_restore_height=int(m.get("restore_height") or 0))

    @_safe
    def set_privacy(self, opts: dict) -> dict:
        cfg = config.load()
        tor_before = dict(cfg.get("tor", {}))
        if "tor_proxy" in opts:
            net.parse_proxy(str(opts["tor_proxy"]))  # validate
            cfg["tor"]["proxy"] = str(opts["tor_proxy"]).strip()
        if "tor_enabled" in opts:
            cfg["tor"]["enabled"] = bool(opts["tor_enabled"])
        for k_ui, k_cfg in (("mode", "mode"), ("hide_balances", "hide_balances")):
            if k_ui in opts:
                cfg["privacy"][k_cfg] = bool(opts[k_ui])
        config.save(cfg)
        with self._lock:
            self._cfg = cfg
            self._apply_network()
            reloaded = False
            if cfg["tor"] != tor_before and self._keyring:
                # Rebuild so nothing keeps using the old route (and Monero restarts with --proxy).
                self._load_portfolio(self._portfolio)
                reloaded = True
        return _ok(reloaded=reloaded)

    @_safe
    def test_tor(self, proxy_text: str = "") -> dict:
        """Checks the proxy really is Tor (asks check.torproject.org through it)."""
        import json
        import ssl as _ssl
        from . import socks as _socks
        px = net.parse_proxy(proxy_text or self._cfg.get("tor", {}).get("proxy", "127.0.0.1:9050"))
        op = _socks.opener(px, _ssl.create_default_context(), "oc-torcheck", secrets.token_hex(4))
        try:
            with op.open("https://check.torproject.org/api/ip", timeout=60) as r:
                d = json.loads(r.read(10_000))
        except OSError as e:
            return _err(f"No connection through {px[0]}:{px[1]}: {e}")
        return _ok(is_tor=bool(d.get("IsTor")), ip=str(d.get("IP", "")))

    @_safe
    def monero_setup(self, wallet_rpc_path: str, daemon: str, restore_height=0, enabled: bool = True) -> dict:
        from pathlib import Path
        from .chains.monero import DAEMON_RE
        p = Path(str(wallet_rpc_path).strip().strip('"'))
        if enabled:
            if not p.is_file() or not p.name.lower().startswith("monero-wallet-rpc"):
                return _err("Choose the monero-wallet-rpc program from the official Monero CLI download.")
            if not DAEMON_RE.match(str(daemon).strip()):
                return _err("Node address should look like node.example.org:18089 or http://xyz.onion:18081.")
        h = int(restore_height or 0)
        if h < 0:
            return _err("Restore height can't be negative.")
        cfg = config.load()
        cfg["monero"].update(enabled=bool(enabled), wallet_rpc_path=str(p) if enabled else cfg["monero"].get("wallet_rpc_path", ""),
                             daemon=str(daemon).strip(), restore_height=h)
        config.save(cfg)
        with self._lock:
            self._cfg = cfg
            if self._keyring:
                for c in [c for c in self._chains if c.key == "xmr"]:
                    c.close()
                    self._chains.remove(c)
                self._index_accounts()
                self._monero_state, self._monero_error = ("idle" if enabled else "off"), ""
        return _ok()

    @_safe
    def pick_file(self) -> dict:
        if self._window is None:
            return _err("File picker unavailable.")
        import webview
        res = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False)
        return _ok(path=res[0] if res else "")

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
