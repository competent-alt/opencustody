"""Minimal SOCKS5 client (RFC 1928 + RFC 1929) for routing traffic through Tor.

* Hostnames are sent to the proxy unresolved (ATYP 0x03), so DNS lookups also go
  through Tor and don't leak to your ISP.
* Username/password auth is used only as an isolation key: Tor's default
  IsolateSOCKSAuth gives each distinct credential its own circuit, so traffic for
  different portfolios can't be linked by exit IP.
"""

from __future__ import annotations

import http.client
import socket
import ssl
import struct
import urllib.request

_REPLIES = {
    1: "general failure", 2: "connection not allowed", 3: "network unreachable", 4: "host unreachable",
    5: "connection refused", 6: "TTL expired", 7: "command not supported", 8: "address type not supported",
}


class SocksError(OSError):
    pass


def _recv_exact(s: socket.socket, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        chunk = s.recv(n - len(buf))
        if not chunk:
            raise SocksError("Proxy closed the connection.")
        buf += chunk
    return buf


def connect(proxy: tuple[str, int], host: str, port: int, timeout: float,  # nosec B107 - SOCKS creds are Tor isolation labels, not secrets
            username: str = "", password: str = "") -> socket.socket:
    try:
        s = socket.create_connection(proxy, timeout=timeout)
    except OSError as e:
        raise SocksError(f"Can't reach the Tor/SOCKS proxy at {proxy[0]}:{proxy[1]} ({e}). "
                         "Is Tor running?") from None
    try:
        auth = bool(username)
        s.sendall(b"\x05\x01" + (b"\x02" if auth else b"\x00"))
        ver, method = _recv_exact(s, 2)
        if ver != 5 or method == 0xFF:
            raise SocksError("Proxy refused our authentication method.")
        if method == 2:
            u, p = username.encode()[:255], password.encode()[:255]
            s.sendall(b"\x01" + bytes([len(u)]) + u + bytes([len(p)]) + p)
            if _recv_exact(s, 2)[1] != 0:
                raise SocksError("Proxy rejected the credentials.")
        elif method != 0:
            raise SocksError("Proxy chose an unsupported method.")
        h = host.encode("idna")
        if len(h) > 255:
            raise SocksError("Hostname too long.")
        s.sendall(b"\x05\x01\x00\x03" + bytes([len(h)]) + h + struct.pack(">H", port))
        ver, rep, _, atyp = _recv_exact(s, 4)
        if ver != 5 or rep != 0:
            raise SocksError(f"Proxy couldn't connect to {host}: {_REPLIES.get(rep, rep)}.")
        if atyp == 1:
            _recv_exact(s, 4 + 2)
        elif atyp == 4:
            _recv_exact(s, 16 + 2)
        elif atyp == 3:
            _recv_exact(s, _recv_exact(s, 1)[0] + 2)
        else:
            raise SocksError("Bad proxy reply.")
        return s
    except BaseException:
        s.close()
        raise


class _Conn(http.client.HTTPConnection):
    def __init__(self, *a, socks_args=None, **kw):
        super().__init__(*a, **kw)
        self._socks_args = socks_args

    def connect(self):
        self.sock = connect(*self._socks_args[:1], self.host, self.port, self.timeout, *self._socks_args[1:])


class _TlsConn(http.client.HTTPSConnection):
    def __init__(self, *a, socks_args=None, **kw):
        super().__init__(*a, **kw)
        self._socks_args = socks_args

    def connect(self):
        raw = connect(*self._socks_args[:1], self.host, self.port, self.timeout, *self._socks_args[1:])
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)


class _HttpHandler(urllib.request.HTTPHandler):
    def __init__(self, socks_args):
        super().__init__()
        self._a = socks_args

    def http_open(self, req):
        return self.do_open(lambda host, **kw: _Conn(host, socks_args=self._a, **kw), req)


class _HttpsHandler(urllib.request.HTTPSHandler):
    def __init__(self, socks_args, context: ssl.SSLContext):
        super().__init__(context=context)
        self._a = socks_args
        self._ctx = context

    def https_open(self, req):
        return self.do_open(lambda host, **kw: _TlsConn(host, socks_args=self._a, **kw), req, context=self._ctx)


def opener(proxy: tuple[str, int], context: ssl.SSLContext, username: str = "", password: str = ""):  # nosec B107 - isolation labels
    """A urllib opener whose every connection goes through the SOCKS proxy.
    ProxyHandler({}) stops environment HTTP proxies from taking over."""
    args = (proxy, username, password)
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _HttpHandler(args), _HttpsHandler(args, context))
