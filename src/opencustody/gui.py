"""Desktop window: the HTML interface in web/ rendered by pywebview.

pywebview uses the operating system's own web engine (Edge WebView2 on
Windows, WebKit on macOS, GTK WebKit on Linux). The page loads only local
files, has a strict Content-Security-Policy with no network access, and can
only call the methods of `WalletApi`.
"""

from __future__ import annotations

from pathlib import Path

import webview

from . import __version__
from .webapi import WalletApi

WEB_DIR = Path(__file__).with_name("web")


def main() -> None:
    api = WalletApi()
    window = webview.create_window(
        f"OpenCustody {__version__}",
        url=str(WEB_DIR / "index.html"),
        js_api=api,
        width=1180,
        height=760,
        min_size=(900, 600),
        background_color="#000000",
        text_select=True,
    )

    def on_locked():
        try:
            window.evaluate_js("window.__locked && window.__locked()")
        except Exception:  # nosec B110 - window may be closing
            pass

    api._on_locked = on_locked
    api._window = window
    window.events.closed += lambda: api.lock()
    # http_server: serve web/ from a private 127.0.0.1 port so the CSP's 'self' works.
    # private_mode: no cookies, cache or local storage persisted to disk.
    webview.start(http_server=True, private_mode=True, debug=False)


if __name__ == "__main__":
    main()
