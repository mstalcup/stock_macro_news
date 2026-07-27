from __future__ import annotations

import json
import re

from .config import MIN_PICKS

VALID_TICKER_RE = re.compile(r"^[A-Z]{1,6}$")
BLOCKLIST = frozenset({"FUTURES", "NASDAQ", "SP500", "CRYPTO", "STOCKS", "MARKET", "NEWS"})


def extract_json_obj(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start, end = raw.find("{"), raw.rfind("}")
    if start >= 0 and end > start:
        try:
            return json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return {}
    return {}


def normalize_ticker(value: str) -> str:
    t = (value or "").strip().upper().replace("$", "")
    if t in {"BTCUSD", "XBTUSD"}:
        return "BTC"
    if t == "ETHUSD":
        return "ETH"
    if not t or t in BLOCKLIST or not VALID_TICKER_RE.match(t):
        return ""
    return t


def normalize_picks(parsed: dict) -> dict:
    bias = (parsed.get("market_bias") or "mixed").strip().lower()
    if bias not in ("risk_on", "risk_off", "mixed", "unclear"):
        bias = "mixed"
    picks = []
    for p in parsed.get("picks") or []:
        if not isinstance(p, dict):
            continue
        sym = normalize_ticker(p.get("ticker") or "")
        if not sym:
            continue
        direction = (p.get("direction") or "long").strip().lower()
        if direction not in ("long", "short"):
            direction = "long"
        conviction = (p.get("conviction") or "medium").strip().lower()
        if conviction not in ("high", "medium", "low"):
            conviction = "medium"
        themes = p.get("themes") or []
        if not isinstance(themes, list):
            themes = []
        picks.append(
            {
                "ticker": sym,
                "direction": direction,
                "conviction": conviction,
                "themes": [str(x).strip() for x in themes if str(x).strip()][:6],
                "rationale": (p.get("rationale") or "").strip()[:500],
                "catalysts": (p.get("catalysts") or "").strip()[:400],
            }
        )
    return {"market_bias": bias, "picks": picks[:5]}


def require_picks(parsed: dict, *, raw: str, min_picks: int = MIN_PICKS) -> dict:
    """Reject empty or truncated panel output."""
    count = len(parsed.get("picks") or [])
    if count >= min_picks:
        return parsed
    raw_len = len((raw or "").strip())
    hint = "truncated JSON" if raw_len > 0 and not (raw or "").strip().endswith("}") else "no picks"
    raise ValueError(
        f"expected at least {min_picks} picks, got {count} ({hint}, raw_len={raw_len})"
    )
