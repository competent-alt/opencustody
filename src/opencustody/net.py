"""Minimal HTTP client on the standard library (fewer dependencies to audit)."""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from urllib.parse import urlparse

from . import __version__

MAX_RESPONSE = 8 * 1024 * 1024
TIMEOUT = 25
_CTX = ssl.create_default_context()
_UA = f"OpenCustody/{__version__}"


class NetError(Exception):
    pass


def _check_url(url: str) -> None:
    u = urlparse(url)
    local = u.hostname in ("localhost", "127.0.0.1", "::1")
    if u.scheme != "https" and not (u.scheme == "http" and local):
        raise NetError(f"Refusing non-HTTPS endpoint: {url}")


def request(url: str, data: bytes | None = None, headers: dict | None = None,
            method: str | None = None, opener=None) -> bytes:
    """HTTP request with backoff on rate limiting (429/503)."""
    for attempt in range(5):
        try:
            return _request_once(url, data, headers, method, opener)
        except _RateLimited:
            time.sleep(min(2 ** attempt, 10))
    raise NetError(f"{urlparse(url).hostname} is rate-limiting requests; try again later "
                   "or configure your own endpoint.")


class _RateLimited(Exception):
    pass


def _request_once(url, data, headers, method, opener) -> bytes:
    _check_url(url)
    h = {"User-Agent": _UA, "Accept": "application/json"}
    h.update(headers or {})
    req = urllib.request.Request(url, data=data, headers=h, method=method)  # noqa: S310 (scheme checked above)
    try:
        if opener is not None:
            resp = opener.open(req, timeout=TIMEOUT)
        else:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT, context=_CTX)  # nosec B310 - scheme checked in _check_url
        with resp:
            body = resp.read(MAX_RESPONSE + 1)
    except urllib.error.HTTPError as e:
        if e.code in (429, 503):
            raise _RateLimited() from None
        detail = e.read(2000).decode("utf-8", "replace")
        raise NetError(f"HTTP {e.code} from {urlparse(url).hostname}: {detail}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise NetError(f"Network error contacting {urlparse(url).hostname}: {e}") from None
    if len(body) > MAX_RESPONSE:
        raise NetError("Response too large.")
    return body


def get_json(url: str, **kw):
    return json.loads(request(url, **kw))


def post_json(url: str, payload, **kw):
    headers = {"Content-Type": "application/json"}
    headers.update(kw.pop("headers", {}) or {})
    return json.loads(request(url, data=json.dumps(payload).encode(), headers=headers, method="POST", **kw))


def post_text(url: str, text: str) -> str:
    return request(url, data=text.encode(), headers={"Content-Type": "text/plain"}, method="POST").decode()


class JsonRpc:
    def __init__(self, url: str, opener=None):
        self.url = url
        self.opener = opener
        self._id = 0

    def call(self, method: str, params=None):
        self._id += 1
        payload = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            payload["params"] = params
        resp = post_json(self.url, payload, opener=self.opener)
        if resp.get("error"):
            err = resp["error"]
            msg = err.get("message", err) if isinstance(err, dict) else err
            raise NetError(f"{method}: {msg}")
        return resp.get("result")
