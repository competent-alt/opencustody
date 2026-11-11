"""Monero via the official monero-wallet-rpc.

Monero's RingCT signing is intricate; reimplementing it would be the most
likely place for a fatal bug. So this module only *drives* the reference
wallet from the Monero Project (download it from getmonero.org and verify its
hash). The spend key is passed to that local process over loopback with
digest authentication, and the wallet file it writes is encrypted with a
password derived from your recovery phrase.
"""

from __future__ import annotations

import json
import re
import secrets
import socket
import ssl
import subprocess  # nosec B404 - we launch one user-selected binary with a fixed argv, no shell
import sys
import time
import urllib.request
from pathlib import Path

from .. import net, socks
from ..paths import monero_dir
from .base import Asset, Chain, HistoryItem, InsufficientFunds, PreparedTx

WALLET_NAME = "opencustody"
DAEMON_RE = re.compile(r"^(https?://)?[A-Za-z0-9.\-]+(:\d{1,5})?/?$")


def daemon_height(daemon: str) -> int:
    url = daemon if daemon.startswith("http") else "http://" + daemon
    req = urllib.request.Request(url.rstrip("/") + "/get_height", headers={"User-Agent": "Mozilla/5.0"})
    px = net.proxy()
    if px:  # through Tor too (fail-closed)
        r = socks.opener(px, ssl.create_default_context(), "oc-monero", "monero").open(req, timeout=60)
    else:
        r = urllib.request.urlopen(req, timeout=20)  # nosec B310 - daemon URL validated by DAEMON_RE
    with r:
        return int(json.loads(r.read(100_000))["height"])


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class MoneroChain(Chain):
    key = "xmr"
    name = "Monero"
    supports_max = True

    def __init__(self, cfg: dict, keyring, restore_height: int):
        self.native = Asset("XMR", 12)
        self.explorer_tx = cfg["explorer"] + "/tx/{}"
        self.explorer_addr = cfg["explorer"] + "/search?value={}"
        path = Path(cfg.get("wallet_rpc_path") or "")
        if not path.is_file() or not path.name.lower().startswith("monero-wallet-rpc"):
            raise RuntimeError("Set monero.wallet_rpc_path to the official monero-wallet-rpc binary.")
        daemon = (cfg.get("daemon") or "").strip()
        if not DAEMON_RE.match(daemon):
            raise RuntimeError("Set monero.daemon to a node address like node.example.org:18089.")
        self._keys = keyring.monero_keys()
        n = getattr(keyring, "n", 0)
        wallet_name = WALLET_NAME if not n else f"{WALLET_NAME}-{n}"  # one wallet file per portfolio
        wallet_pw = keyring.derived_secret("monero-wallet-file")
        rpc_pw = secrets.token_hex(24)
        port = _free_port()
        wdir = monero_dir()
        flags = 0x08000000 if sys.platform == "win32" else 0  # CREATE_NO_WINDOW
        argv = [str(path), "--rpc-bind-ip", "127.0.0.1", "--rpc-bind-port", str(port),
                "--rpc-login", f"opencustody:{rpc_pw}", "--wallet-dir", str(wdir),
                "--daemon-address", daemon, "--log-file", str(wdir / "wallet-rpc.log"), "--max-log-files", "2"]
        px = net.proxy()
        if px:
            argv += ["--proxy", f"{px[0]}:{px[1]}"]  # wallet <-> node traffic through Tor
        self._proc = subprocess.Popen(  # nosec B603 - fixed argv list, no shell
            argv,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
        url = f"http://127.0.0.1:{port}/json_rpc"
        pm = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        pm.add_password(None, url, "opencustody", rpc_pw)
        opener = urllib.request.build_opener(urllib.request.HTTPDigestAuthHandler(pm))
        self.rpc = net.JsonRpc(url, opener=opener)
        self._wait_ready()
        if (wdir / wallet_name).exists():
            self.rpc.call("open_wallet", {"filename": wallet_name, "password": wallet_pw})
        else:
            self.rpc.call("generate_from_keys", {
                "filename": wallet_name, "address": self._keys.address, "spendkey": self._keys.private_spend,
                "viewkey": self._keys.private_view, "password": wallet_pw, "restore_height": int(restore_height),
                "autosave_current": True})
        got = self.rpc.call("get_address", {"account_index": 0})["address"]
        if got != self._keys.address:
            raise RuntimeError("Monero wallet file does not match this recovery phrase.")

    def _wait_ready(self) -> None:
        deadline = time.time() + 45
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError("monero-wallet-rpc exited; see wallet-rpc.log in the data folder.")
            try:
                self.rpc.call("get_version")
                return
            except net.NetError:
                time.sleep(0.5)
        raise RuntimeError("monero-wallet-rpc did not start in time.")

    def address(self) -> str:
        return self._keys.address

    def balance(self, asset: Asset) -> int:
        self.rpc.call("refresh")
        return int(self.rpc.call("get_balance", {"account_index": 0})["balance"])

    def validate_address(self, addr: str) -> str | None:
        r = self.rpc.call("validate_address", {"address": addr.strip(), "any_net_type": False})
        if not r.get("valid"):
            return "Not a valid Monero mainnet address."
        return None

    def prepare_send(self, asset: Asset, to: str, amount: int | None) -> PreparedTx:
        to = to.strip()
        err = self.validate_address(to)
        if err:
            raise ValueError(err)
        self.rpc.call("refresh")
        try:
            if amount is None:
                r = self.rpc.call("sweep_all", {"address": to, "account_index": 0, "do_not_relay": True,
                                                "get_tx_metadata": True})
                if len(r.get("tx_metadata_list", [])) != 1:
                    raise RuntimeError("Sweep needs more than one transaction; send in smaller amounts.")
                meta, amt, fee = r["tx_metadata_list"][0], r["amount_list"][0], r["fee_list"][0]
            else:
                r = self.rpc.call("transfer", {"destinations": [{"amount": amount, "address": to}],
                                               "account_index": 0, "priority": 0, "do_not_relay": True,
                                               "get_tx_metadata": True})
                meta, amt, fee = r["tx_metadata"], r["amount"], r["fee"]
        except net.NetError as e:
            if "not enough" in str(e).lower():
                raise InsufficientFunds("Not enough unlocked XMR (funds need 10 confirmations).") from None
            raise
        if amount is not None and amt != amount:
            raise RuntimeError("Wallet built a transaction for a different amount; refusing.")

        def send() -> str:
            return self.rpc.call("relay_tx", {"hex": meta})["tx_hash"]

        return PreparedTx(self.name, asset, self.address(), to, int(amt), int(fee), "", self.native, send)

    def history(self, asset: Asset, limit: int = 25) -> list[HistoryItem]:
        r = self.rpc.call("get_transfers", {"in": True, "out": True, "pending": True, "pool": True,
                                            "failed": True, "account_index": 0})
        out = []
        for kind in ("in", "out", "pending", "pool", "failed"):
            for t in r.get(kind, []):
                d = "out" if kind in ("out", "pending", "failed") else "in"
                party = ((t.get("destinations") or [{}])[0].get("address", "") if d == "out" else "")
                status = {"pending": "pending", "pool": "pending", "failed": "failed"}.get(kind, "confirmed")
                amt = int(t.get("amount", 0)) + (int(t.get("fee", 0)) if d == "out" else 0)
                out.append(HistoryItem(t["txid"], t.get("timestamp"), d, amt, party, status))
        out.sort(key=lambda h: h.time or 2**62, reverse=True)
        return out[:limit]

    def close(self) -> None:
        try:
            self.rpc.call("store")
            self.rpc.call("close_wallet")
        except Exception:  # nosec B110 - best-effort shutdown
            pass
        self._proc.terminate()
        try:
            self._proc.wait(10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
