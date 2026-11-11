import socket
import socketserver
import struct
import threading

import pytest

from opencustody import net, webapi
from opencustody.chains import utxo
from opencustody.keys import Keyring

ABANDON = "abandon " * 11 + "about"
PW = "correct horse battery"


class FakeSocks(socketserver.ThreadingTCPServer):
    """SOCKS5 server that records what clients asked for and answers plain HTTP itself."""
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self):
        self.seen: list[dict] = []
        super().__init__(("127.0.0.1", 0), _Handler)


class _Handler(socketserver.BaseRequestHandler):
    def handle(self):
        s = self.request
        ver, n = s.recv(2)
        methods = s.recv(n)
        rec = {}
        if 2 in methods:
            s.sendall(b"\x05\x02")
            s.recv(1)
            user = s.recv(s.recv(1)[0]).decode()
            pw = s.recv(s.recv(1)[0]).decode()
            rec.update(user=user, password=pw)
            s.sendall(b"\x01\x00")
        else:
            s.sendall(b"\x05\x00")
        _, cmd, _, atyp = s.recv(4)
        rec["atyp"] = atyp
        rec["host"] = s.recv(s.recv(1)[0]).decode() if atyp == 3 else None
        rec["port"] = struct.unpack(">H", s.recv(2))[0]
        self.server.seen.append(rec)
        s.sendall(b"\x05\x00\x00\x01" + socket.inet_aton("0.0.0.0") + b"\x00\x00")
        s.recv(65536)  # the HTTP request
        body = b'{"ok": true}'
        s.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                  + str(len(body)).encode() + b"\r\nConnection: close\r\n\r\n" + body)


@pytest.fixture
def fake_socks():
    srv = FakeSocks()
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield srv
    srv.shutdown()
    srv.server_close()
    net.configure(None)


def test_requests_go_through_socks_with_remote_dns(fake_socks):
    net.configure(f"127.0.0.1:{fake_socks.server_address[1]}")
    # http to a .onion is allowed only via Tor; the fake proxy answers it.
    assert net.get_json("http://exampleonionaddressxyz.onion/api") == {"ok": True}
    rec = fake_socks.seen[-1]
    assert rec["atyp"] == 3 and rec["host"] == "exampleonionaddressxyz.onion"  # hostname, not a resolved IP
    assert rec["port"] == 80


def test_portfolios_get_separate_circuits(fake_socks):
    net.configure(f"127.0.0.1:{fake_socks.server_address[1]}")
    with net.circuit("portfolio-0"):
        net.get_json("http://aaaa.onion/")
    with net.circuit("portfolio-1"):
        net.get_json("http://aaaa.onion/")
    with net.circuit("portfolio-0"):
        net.get_json("http://aaaa.onion/")
    users = [r["user"] for r in fake_socks.seen]
    assert users[0] != users[1] and users[0] == users[2]  # Tor IsolateSOCKSAuth key


def test_fail_closed_when_proxy_down():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        dead = s.getsockname()[1]
    net.configure(f"127.0.0.1:{dead}")
    try:
        with pytest.raises(net.NetError, match="Tor"):
            net.get_json("https://example.com/")
    finally:
        net.configure(None)


def test_http_to_clearnet_refused_even_with_tor():
    net.configure("127.0.0.1:9")
    try:
        with pytest.raises(net.NetError, match="non-HTTPS"):
            net.get_json("http://example.com/")
    finally:
        net.configure(None)


def test_parse_proxy():
    assert net.parse_proxy("127.0.0.1:9150") == ("127.0.0.1", 9150)
    for bad in ("", "127.0.0.1", "host:0", "host:99999", "host:abc"):
        with pytest.raises(ValueError):
            net.parse_proxy(bad)


# ---- privacy mode via the bridge -------------------------------------------------------
@pytest.fixture
def api(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENCUSTODY_HOME", str(tmp_path))
    a = webapi.WalletApi()
    a.restore_mnemonic(ABANDON)
    assert a.save_wallet(PW, PW)["ok"]
    yield a
    net.configure(None)


def test_privacy_mode(api, monkeypatch):
    opened = []
    monkeypatch.setattr(webapi.webbrowser, "open", opened.append)
    assert api.set_privacy({"mode": True, "hide_balances": True})["ok"]
    st = api.privacy_status()
    assert st["mode"] and st["hide_balances"] and not st["prices_enabled"]
    assert api.prices()["enabled"] is False
    r = api.history("eth:0")
    assert r["ok"] and r["unavailable"].startswith("Privacy mode")
    r = api.open_url("https://etherscan.io/tx/0x1")
    assert r["copy"] == "https://etherscan.io/tx/0x1" and opened == []  # not opened in the normal browser


def test_tor_setting_applies_and_validates(api):
    assert not api.set_privacy({"tor_proxy": "nonsense"})["ok"]
    assert api.set_privacy({"tor_enabled": True, "tor_proxy": "127.0.0.1:9150"})["ok"]
    assert net.proxy() == ("127.0.0.1", 9150)
    assert api.privacy_status()["tor_enabled"]
    assert api.set_privacy({"tor_enabled": False})["ok"] and net.proxy() is None


def test_endpoint_audit(api):
    eps = api.privacy_status()["endpoints"]
    assert eps and all(not e["own"] for e in eps)  # all defaults are public


def test_monero_setup_validation(api, tmp_path):
    assert not api.monero_setup(str(tmp_path / "nope.exe"), "node:18081")["ok"]
    fake = tmp_path / "monero-wallet-rpc.exe"
    fake.write_bytes(b"")
    assert not api.monero_setup(str(fake), "bad host; rm -rf")["ok"]
    assert api.monero_setup(str(fake), "xmrnode.example.org:18089", 3_000_000)["ok"]
    st = api.privacy_status()
    assert st["monero_enabled"] and st["monero_restore_height"] == 3_000_000 and st["monero_state"] == "idle"
    assert api.monero_setup("", "", 0, enabled=False)["ok"] and not api.privacy_status()["monero_enabled"]


# ---- coin control ----------------------------------------------------------------------
def test_spend_exactly():
    a = utxo.Utxo("00" * 32, 0, 50_000, 0, 0)
    b = utxo.Utxo("11" * 32, 1, 20_000, 0, 1)
    ins, amt, change = utxo.spend_exactly([a, b], 30_000, 2)
    assert ins == [a, b] and amt == 30_000 and change == 70_000 - 30_000 - utxo.vsize(2, 2) * 2
    ins, amt, change = utxo.spend_exactly([b], None, 2)
    assert amt == 20_000 - utxo.vsize(1, 1) * 2 and change == 0
    with pytest.raises(Exception):
        utxo.spend_exactly([b], 20_000, 2)


def test_coin_control_send_and_linking_warning(monkeypatch):
    kr = Keyring(ABANDON).portfolio(0)
    c = utxo.UtxoChain("btc", {"esplora": "https://x", "explorer": "https://e", "gap_limit": 1}, kr)
    a0, a1 = kr.bip84_key("btc", 0, 0)[2], kr.bip84_key("btc", 0, 1)[2]
    utxos = {a0: [{"txid": "aa" * 32, "vout": 0, "value": 60_000, "status": {"confirmed": True}}],
             a1: [{"txid": "bb" * 32, "vout": 1, "value": 40_000, "status": {"confirmed": True}}]}

    def fake(url, **kw):
        if url.endswith("/fee-estimates"):
            return {"3": 2}
        if url.endswith("/utxo"):
            return utxos.get(url.split("/")[-2], [])
        addr = url.rsplit("/", 1)[1]
        n = 1 if addr in utxos else 0
        return {"chain_stats": {"tx_count": n, "funded_txo_sum": 0, "spent_txo_sum": 0},
                "mempool_stats": {"tx_count": 0, "funded_txo_sum": 0, "spent_txo_sum": 0}}

    monkeypatch.setattr(utxo.net, "get_json", fake)
    coins = c.coins(c.native)
    assert [x["id"] for x in coins] == [f"{'aa' * 32}:0", f"{'bb' * 32}:1"]
    dest = "bc1qnjg0jd8228aq7egyzacy8cys3knf9xvrerkf9g"
    # Only the 40k coin: one address, no linking warning.
    ptx = c.prepare_send(c.native, dest, 30_000, coins=[f"{'bb' * 32}:1"])
    assert ptx.amount == 30_000 and not ptx.warnings
    # Automatic selection for 90k must merge both addresses -> warning.
    ptx = c.prepare_send(c.native, dest, 90_000)
    assert any("links them together" in w for w in ptx.warnings)
    with pytest.raises(ValueError):
        c.prepare_send(c.native, dest, 1000, coins=["cc" * 32 + ":0"])
