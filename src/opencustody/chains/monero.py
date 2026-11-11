"""Monero via the official monero-wallet-rpc.

Monero's RingCT signing is intricate; reimplementing it would be the most
likely place for a fatal bug. So this module only *drives* the reference
wallet from the Monero Project (download it from getmonero.org and verify its
hash). The spend key is passed to that local process over loopback with
digest authentication, and the wallet file it writes is encrypted with a
password derived from your recovery phrase.
"""

from __future__ import annotations

import hashlib
import http.client
import json
import re
import secrets
import socket
import threading
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


class WalletRpcClient:
    """JSON-RPC client for the local monero-wallet-rpc with HTTP digest auth.

    Monero's server ties each digest nonce to the TCP connection it was issued on, so the
    challenge and the authenticated request must share one kept-alive connection. (urllib
    sends `Connection: close` on every request, which is why it can't log in.) One connection
    is shared under a lock; it reconnects and re-authenticates as needed.
    """

    def __init__(self, port: int, user: str, password: str):
        self._port, self._user, self._pw = port, user, password
        self._conn: http.client.HTTPConnection | None = None
        self._auth: dict | None = None
        self._nc = 0
        self._id = 0
        self._lock = threading.Lock()

    def _digest_header(self) -> str:
        a = self._auth
        self._nc += 1
        nc, cnonce = f"{self._nc:08x}", secrets.token_hex(8)
        ha1 = hashlib.md5(f"{self._user}:{a['realm']}:{self._pw}".encode()).hexdigest()  # nosec B324 - required by HTTP digest
        ha2 = hashlib.md5(b"POST:/json_rpc").hexdigest()  # nosec B324
        resp = hashlib.md5(f"{ha1}:{a['nonce']}:{nc}:{cnonce}:auth:{ha2}".encode()).hexdigest()  # nosec B324
        return (f'Digest username="{self._user}", realm="{a["realm"]}", nonce="{a["nonce"]}", uri="/json_rpc", '
                f'algorithm=MD5, response="{resp}", qop=auth, nc={nc}, cnonce="{cnonce}"')

    def _post(self, body: bytes, timeout: float) -> tuple[int, bytes, str]:
        if self._conn is None:
            self._conn = http.client.HTTPConnection("127.0.0.1", self._port, timeout=timeout)
            self._auth, self._nc = None, 0  # nonces are per connection
        self._conn.timeout = timeout
        if self._conn.sock is not None:
            self._conn.sock.settimeout(timeout)
        headers = {"Content-Type": "application/json"}
        if self._auth:
            headers["Authorization"] = self._digest_header()
        self._conn.request("POST", "/json_rpc", body, headers)
        r = self._conn.getresponse()
        data = r.read(MAX_RPC_RESPONSE + 1)
        return r.status, data, r.getheader("WWW-Authenticate") or ""

    def call(self, method: str, params=None, timeout: float = 120):
        with self._lock:
            self._id += 1
            payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
            if params is not None:
                payload["params"] = params
            body = json.dumps(payload).encode()
            for _attempt in range(3):
                try:
                    status, data, challenge = self._post(body, timeout)
                except (OSError, http.client.HTTPException) as e:
                    self.close()
                    last = e
                    continue
                if status == 401 and challenge.lower().startswith("digest"):
                    self._auth = dict(re.findall(r'(\w+)="?([^",]+)"?', challenge))
                    continue
                if status != 200:
                    raise net.NetError(f"monero-wallet-rpc HTTP {status}")
                if len(data) > MAX_RPC_RESPONSE:
                    raise net.NetError("monero-wallet-rpc response too large")
                resp = json.loads(data)
                if resp.get("error"):
                    err = resp["error"]
                    raise net.NetError(f"{method}: {err.get('message', err) if isinstance(err, dict) else err}")
                return resp.get("result")
            raise net.NetError(f"monero-wallet-rpc not reachable ({locals().get('last', 'authentication failed')})")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except OSError:
                pass
        self._conn, self._auth = None, None


MAX_RPC_RESPONSE = 32 * 1024 * 1024


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
        self.rpc = WalletRpcClient(port, "opencustody", rpc_pw)
        try:
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
        except BaseException:
            self._stop_process()  # never leave a wallet process running after a failed start
            raise

    def _wait_ready(self) -> None:
        deadline = time.time() + 45
        while time.time() < deadline:
            if self._proc.poll() is not None:
                raise RuntimeError("monero-wallet-rpc exited; see wallet-rpc.log in the data folder.")
            try:
                self.rpc.call("get_version", timeout=5)
                return
            except net.NetError:
                time.sleep(0.5)
        raise RuntimeError("monero-wallet-rpc did not start in time.")

    def address(self) -> str:
        return self._keys.address

    def balance(self, asset: Asset) -> int:
        self.rpc.call("refresh", timeout=900)  # first sync can take a while
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
        self.rpc.call("refresh", timeout=900)  # first sync can take a while
        try:
            if amount is None:
                r = self.rpc.call("sweep_all", timeout=300, params={"address": to, "account_index": 0, "do_not_relay": True,
                                                "get_tx_metadata": True})
                if len(r.get("tx_metadata_list", [])) != 1:
                    raise RuntimeError("Sweep needs more than one transaction; send in smaller amounts.")
                meta, amt, fee = r["tx_metadata_list"][0], r["amount_list"][0], r["fee_list"][0]
            else:
                r = self.rpc.call("transfer", timeout=300, params={"destinations": [{"amount": amount, "address": to}],
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
            self.rpc.call("store", timeout=30)
            self.rpc.call("close_wallet", timeout=30)
        except Exception:  # nosec B110 - best-effort shutdown
            pass
        self._stop_process()

    def _stop_process(self) -> None:
        self.rpc.close()
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
