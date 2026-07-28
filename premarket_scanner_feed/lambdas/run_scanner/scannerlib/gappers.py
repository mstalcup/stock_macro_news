from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .catalyst import lookup_catalyst
from .yahoo import (
    fetch_day_gainers,
    fetch_gainers_page_fallback,
    fetch_quote,
    gap_metrics,
    market_issue_date,
)

ET = ZoneInfo("America/New_York")
LOG = logging.getLogger(__name__)

DEFAULT_MIN_GAP = 5.0
DEFAULT_MIN_PRICE = 3.0
DEFAULT_MIN_VOLUME = 50_000
DEFAULT_TOP_N = 10


def scan_gappers(
    *,
    finnhub_key: str = "",
    min_gap: float = DEFAULT_MIN_GAP,
    min_price: float = DEFAULT_MIN_PRICE,
    min_volume: float = DEFAULT_MIN_VOLUME,
    top_n: int = DEFAULT_TOP_N,
) -> dict[str, Any]:
    scanned_at = datetime.now(tz=ET).isoformat()
    issue_date = market_issue_date()

    raw = fetch_day_gainers(50)
    if len(raw) < 5:
        LOG.info("screener thin (%d); trying HTML fallback", len(raw))
        raw = fetch_gainers_page_fallback() or raw

    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw:
        sym = str(row.get("symbol") or "").upper().strip()
        if not sym or sym in seen:
            continue
        seen.add(sym)
        q = row
        # Screener rows often miss premarket fields — refresh quote
        need_refresh = q.get("preMarketPrice") is None and q.get("regularMarketPreviousClose") is None
        if need_refresh or q.get("preMarketChangePercent") is None:
            fresh = fetch_quote(sym)
            if fresh:
                q = {**q, **fresh}
        m = gap_metrics(q)
        gap = m["gap_pct"]
        price = m["price"]
        vol = m["volume"]
        if gap is None or price is None or vol is None:
            continue
        if gap <= min_gap or price <= min_price or vol < min_volume:
            continue
        candidates.append(
            {
                "symbol": sym,
                "price": round(float(price), 4),
                "gap_pct": round(float(gap), 4),
                "premarket_volume": int(vol),
            }
        )

    candidates.sort(key=lambda x: x["gap_pct"], reverse=True)
    top = candidates[:top_n]

    gappers: list[dict[str, Any]] = []
    for i, row in enumerate(top, start=1):
        cat = lookup_catalyst(row["symbol"], finnhub_key=finnhub_key)
        gappers.append(
            {
                "rank": i,
                "symbol": row["symbol"],
                "price": row["price"],
                "gap_pct": row["gap_pct"],
                "premarket_volume": row["premarket_volume"],
                "catalyst": cat.get("catalyst"),
                "headlines": cat.get("headlines") or [],
                "catalyst_source": cat.get("source"),
            }
        )

    return {
        "scanned_at": scanned_at,
        "issue_date": issue_date,
        "filters": {
            "min_gap_pct": min_gap,
            "min_price": min_price,
            "min_volume": min_volume,
            "top_n": top_n,
        },
        "universe_size": len(candidates),
        "gappers": gappers,
    }


def one_line_summary(payload: dict[str, Any]) -> str:
    gappers = payload.get("gappers") or []
    n = len(gappers)
    tops = []
    for g in gappers[:3]:
        cat = g.get("catalyst") or "no catalyst"
        if len(cat) > 60:
            cat = cat[:57] + "..."
        tops.append(f"{g['symbol']} ({g['gap_pct']}%) — {cat}")
    top_s = ", ".join(tops) if tops else "(none)"
    return f"Premarket Gappers: {n} names. Top: {top_s}"
