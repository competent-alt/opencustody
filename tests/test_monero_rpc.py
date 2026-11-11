"""WalletRpcClient against a fake server that, like monero-wallet-rpc, ties digest nonces
to the TCP connection they were issued on."""

import hashlib
import json
import re
import secrets
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from opencustody import net
from opencustody.chains.monero import WalletRpcClient

USER, PW, REALM = "opencustody", "s3cret", "monero-rpc"


class _H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive: one handler instance per connection

    def log_message(self, *a):
        pass

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        auth = self.headers.get("Authorization", "")
        nonce = getattr(self, "nonce", None)
        if not nonce or not auth.startswith("Digest"):
            return self._challenge()
        f = dict(re.findall(r'(\w+)="?([^",]+)"?', auth))
        md5 = lambda s: hashlib.md5(s.encode()).hexdigest()
        want = md5(f"{md5(f'{USER}:{REALM}:{PW}')}:{nonce}:{f['nc']}:{f['cnonce']}:auth:{md5('POST:/json_rpc')}")
        if f.get("nonce") != nonce or f.get("response") != want:
            return self._challenge()
        self.server.calls += 1
        req = json.loads(body)
        out = {"jsonrpc": "2.0", "id": req["id"]}
        if req["method"] == "boom":
            out["error"] = {"code": -1, "message": "not enough money"}
        else:
            out["result"] = {"echo": req["method"]}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _challenge(self):
        self.nonce = secrets.token_urlsafe(12)  # per connection, like epee
        self.send_response(401)
        self.send_header("WWW-Authenticate", f'Digest qop="auth",algorithm=MD5,realm="{REALM}",nonce="{self.nonce}",stale=false')
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _H)
    srv.calls = 0
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()


def test_digest_on_kept_alive_connection(server):
    c = WalletRpcClient(server.server_address[1], USER, PW)
    assert c.call("get_version") == {"echo": "get_version"}
    assert c.call("get_balance", {"account_index": 0}) == {"echo": "get_balance"}
    assert server.calls == 2


def test_reconnects_and_reauthenticates(server):
    c = WalletRpcClient(server.server_address[1], USER, PW)
    c.call("a")
    c.close()  # new connection -> old nonce is invalid -> must re-auth transparently
    assert c.call("b") == {"echo": "b"}


def test_wrong_password_fails(server):
    with pytest.raises(net.NetError):
        WalletRpcClient(server.server_address[1], USER, "wrong").call("x")


def test_rpc_errors_surface(server):
    with pytest.raises(net.NetError, match="not enough money"):
        WalletRpcClient(server.server_address[1], USER, PW).call("boom")
