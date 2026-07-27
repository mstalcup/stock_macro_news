"""Canonical ticker and direction normalization across feeds."""
from __future__ import annotations

import re
from typing import Literal

Direction = Literal["long", "short"]

VALID_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-=]{0,11}$")
BLOCKLIST = frozenset(
    {
        "FUTURES",
        "NASDAQ",
        "SP500",
        "CRYPTO",
        "STOCKS",
        "MARKET",
        "NEWS",
        "THE",
        "AND",
    }
)

YAHOO_ALIASES: dict[str, str] = {
    "BTCUSD": "BTC",
    "XBTUSD": "BTC",
    "ETHUSD": "ETH",
}


def normalize_ticker(value: str) -> str:
    t = (value or "").strip().upper().replace("$", "")
    if not t:
        return ""
    if t in YAHOO_ALIASES:
        t = YAHOO_ALIASES[t]
    if t in BLOCKLIST:
        return ""
    if not VALID_TICKER_RE.match(t):
        return ""
    return t


def normalize_direction(value: str) -> Direction | None:
    """Map feed-specific wording to long/short; None = abstain (mixed/unclear)."""
    x = (value or "").strip().lower()
    if x in ("long", "bull", "bullish", "buy", "positive", "risk_on", "overweight"):
        return "long"
    if x in ("short", "bear", "bearish", "sell", "negative", "risk_off", "underweight"):
        return "short"
    return None


def direction_arrow(direction: Direction | None) -> str:
    if direction == "long":
        return "L"
    if direction == "short":
        return "S"
    return "·"
