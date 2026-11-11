"""USD prices for the portfolio chart (CoinGecko public API, cached).

Privacy: the price service learns which coins are listed, never your addresses.
Disable with "prices": {"enabled": false} in config.json.
"""

from __future__ import annotations

import threading
import time
import urllib.parse

from . import net

COINGECKO_IDS = {
    "BTC": "bitcoin", "LTC": "litecoin", "ETH": "ethereum", "SOL": "solana", "TRX": "tron",
    "TON": "the-open-network", "XMR": "monero", "BNB": "binancecoin", "POL": "polygon-ecosystem-token",
    "USDC": "usd-coin", "USDT": "tether",
}
TTL = 120

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "key": "", "data": {}}


def fetch(api: str, symbols: list[str]) -> dict[str, dict]:
    """{symbol: {"usd": float, "change24h": float|None}} for symbols we know."""
    ids = sorted({COINGECKO_IDS[s] for s in symbols if s in COINGECKO_IDS})
    key = ",".join(ids)
    with _lock:
        if _cache["key"] == key and time.time() - _cache["at"] < TTL:
            return _cache["data"]
    q = urllib.parse.urlencode({"ids": key, "vs_currencies": "usd", "include_24hr_change": "true"})
    raw = net.get_json(f"{api.rstrip('/')}/simple/price?{q}")
    out = {}
    for sym, cid in COINGECKO_IDS.items():
        row = raw.get(cid)
        if isinstance(row, dict) and isinstance(row.get("usd"), (int, float)):
            ch = row.get("usd_24h_change")
            out[sym] = {"usd": float(row["usd"]), "change24h": float(ch) if isinstance(ch, (int, float)) else None}
    with _lock:
        _cache.update(at=time.time(), key=key, data=out)
    return out
