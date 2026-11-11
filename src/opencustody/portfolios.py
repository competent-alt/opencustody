"""Portfolio names. Not secret: just labels for derivation indexes 0, 1, 2…

Every portfolio is derived from the same recovery phrase, so restoring the phrase
restores all of them (recreate one by adding portfolios until its number comes back).
"""

from __future__ import annotations

import json

from .keys import MAX_PORTFOLIOS
from .paths import atomic_write, data_dir

DEFAULT = {"active": 0, "items": [{"n": 0, "name": "Main"}]}


def _path():
    return data_dir() / "portfolios.json"


def load() -> dict:
    p = _path()
    try:
        d = json.loads(p.read_text("utf-8")) if p.exists() else DEFAULT
        items = [{"n": int(i["n"]), "name": clean_name(i["name"])} for i in d["items"]
                 if 0 <= int(i["n"]) < MAX_PORTFOLIOS]
        if not any(i["n"] == 0 for i in items):
            items.insert(0, {"n": 0, "name": "Main"})
        items.sort(key=lambda i: i["n"])
        active = int(d.get("active", 0))
        if active not in {i["n"] for i in items}:
            active = 0
        return {"active": active, "items": items}
    except (ValueError, KeyError, TypeError):
        return json.loads(json.dumps(DEFAULT))


def save(d: dict) -> None:
    atomic_write(_path(), json.dumps(d, indent=2).encode())


def clean_name(name) -> str:
    name = " ".join(str(name).split())[:40]
    if not name:
        raise ValueError("Give the portfolio a name.")
    return name


def add(d: dict, name: str) -> int:
    used = {i["n"] for i in d["items"]}
    n = next((k for k in range(MAX_PORTFOLIOS) if k not in used), None)
    if n is None:
        raise ValueError("Portfolio limit reached.")
    d["items"].append({"n": n, "name": clean_name(name)})
    d["items"].sort(key=lambda i: i["n"])
    return n
