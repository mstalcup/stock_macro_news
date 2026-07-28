from __future__ import annotations

import logging
from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from .yahoo import current_price_from_quote, fetch_daily_bars, fetch_intraday_1m, fetch_quote, market_issue_date

ET = ZoneInfo("America/New_York")
LOG = logging.getLogger(__name__)

WINDOW_START = time(10, 0)
WINDOW_END = time(15, 30)
PM_START = time(4, 0)
RTH_OPEN = time(9, 30)


def in_tjl_window(now: datetime | None = None) -> bool:
    et = (now or datetime.now(tz=ET)).astimezone(ET)
    if et.weekday() >= 5:
        return False
    t = et.time()
    return WINDOW_START <= t <= WINDOW_END


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def evaluate_ticker(symbol: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Trend Join Long checks for one symbol. Sequential-friendly."""
    et_now = (now or datetime.now(tz=ET)).astimezone(ET)
    today = et_now.date()
    sym = symbol.upper().strip()

    daily = fetch_daily_bars(sym, days=260)
    if len(daily) < 200:
        return {
            "symbol": sym,
            "result": "fail_daily",
            "reason": f"insufficient daily bars ({len(daily)})",
        }

    last = daily[-1]
    # If last bar is today and still forming, prefer previous completed daily for "prev" levels
    if last["time"].date() == today and len(daily) >= 2:
        prev = daily[-2]
        closes_for_sma = [b["close"] for b in daily[:-1]][-200:]
    else:
        prev = last
        closes_for_sma = [b["close"] for b in daily][-200:]

    prev_daily_high = float(prev["high"] or prev["close"])
    prev_daily_close = float(prev["close"])
    sma200 = _mean(closes_for_sma)
    if sma200 is None:
        return {"symbol": sym, "result": "fail_daily", "reason": "sma200 unavailable"}

    q = fetch_quote(sym) or {}
    curr_px = current_price_from_quote(q)
    if curr_px is None and daily:
        curr_px = float(daily[-1]["close"])
    if curr_px is None:
        return {"symbol": sym, "result": "fail_daily", "reason": "no current price"}

    bars_1m = fetch_intraday_1m(sym)
    pmh = None
    today_hod = None
    completed = [b for b in bars_1m if b["time"] < et_now]
    for b in completed:
        bt = b["time"]
        if bt.date() != today:
            continue
        h = b.get("high")
        if h is None:
            continue
        t = bt.time()
        if PM_START <= t < RTH_OPEN:
            pmh = h if pmh is None else max(pmh, h)
        elif t >= RTH_OPEN:
            today_hod = h if today_hod is None else max(today_hod, h)

    daily_breakout = (curr_px > prev_daily_high) and (prev_daily_close > sma200)
    intraday_breakout = (
        pmh is not None
        and today_hod is not None
        and curr_px > pmh
        and curr_px > today_hod
    )

    if daily_breakout and intraday_breakout:
        result = "PASS"
        reason = "daily + intraday breakout"
    elif not daily_breakout:
        result = "fail_daily"
        reason = (
            f"curr {curr_px:.2f} vs prev_high {prev_daily_high:.2f}; "
            f"prev_close {prev_daily_close:.2f} vs sma200 {sma200:.2f}"
        )
    else:
        result = "fail_intraday"
        reason = f"curr {curr_px:.2f} vs pmh {pmh} / hod {today_hod}"

    return {
        "symbol": sym,
        "result": result,
        "reason": reason,
        "curr_price": round(float(curr_px), 4),
        "prev_daily_high": round(prev_daily_high, 4),
        "prev_daily_close": round(prev_daily_close, 4),
        "sma200": round(float(sma200), 4),
        "pmh": round(float(pmh), 4) if pmh is not None else None,
        "today_hod": round(float(today_hod), 4) if today_hod is not None else None,
        "daily_breakout": daily_breakout,
        "intraday_breakout": intraday_breakout,
    }


def scan_tjl(
    symbols: list[str],
    *,
    now: datetime | None = None,
    force: bool = False,
) -> dict[str, Any]:
    et_now = (now or datetime.now(tz=ET)).astimezone(ET)
    scanned_at = et_now.isoformat()
    issue_date = market_issue_date(et_now)

    if not force and not in_tjl_window(et_now):
        return {
            "scanned_at": scanned_at,
            "issue_date": issue_date,
            "error": "outside_tjl_window",
            "message": f"TJL only runs 10:00–15:30 ET (now {et_now.strftime('%H:%M %Z')})",
            "candidates_checked": 0,
            "hits": [],
            "all_results": [],
        }

    uniq = []
    seen = set()
    for s in symbols:
        u = str(s or "").upper().strip()
        if u and u not in seen:
            seen.add(u)
            uniq.append(u)

    all_results: list[dict[str, Any]] = []
    hits: list[dict[str, Any]] = []
    for sym in uniq:
        row = evaluate_ticker(sym, now=et_now)
        all_results.append({"symbol": row["symbol"], "result": row["result"], "reason": row.get("reason")})
        if row.get("result") == "PASS":
            hits.append(
                {
                    "symbol": row["symbol"],
                    "curr_price": row.get("curr_price"),
                    "prev_daily_high": row.get("prev_daily_high"),
                    "sma200": row.get("sma200"),
                    "pmh": row.get("pmh"),
                    "today_hod": row.get("today_hod"),
                }
            )
        LOG.info("%s: %s — %s", sym, row.get("result"), row.get("reason"))

    return {
        "scanned_at": scanned_at,
        "issue_date": issue_date,
        "candidates_checked": len(uniq),
        "hits": hits,
        "all_results": all_results,
    }
