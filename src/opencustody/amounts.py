"""Exact decimal <-> base-unit conversion. Never use floats for money."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation


_PLAIN = re.compile(r"^[0-9]{1,40}(\.[0-9]{1,40})?\Z")  # ASCII digits only


def to_base_units(text: str, decimals: int) -> int:
    text = text.strip()
    # Plain digits only: no exponents, signs, separators or locale tricks.
    if not _PLAIN.match(text):
        raise ValueError(f"Not a plain decimal number: {text!r}")
    try:
        d = Decimal(text)
    except InvalidOperation:
        raise ValueError(f"Not a number: {text!r}") from None
    if not d.is_finite() or d <= 0:
        raise ValueError("Amount must be a positive number.")
    scaled = d.scaleb(decimals)
    if scaled != scaled.to_integral_value():
        raise ValueError(f"Too many decimal places (max {decimals}).")
    return int(scaled)


def fmt(units: int, decimals: int) -> str:
    s = format(Decimal(units).scaleb(-decimals), "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s or "0"
