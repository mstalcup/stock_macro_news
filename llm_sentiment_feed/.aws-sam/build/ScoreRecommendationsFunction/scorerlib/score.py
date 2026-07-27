from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from decimal import Decimal

from .prices import calendar_add, fetch_daily_close, fetch_latest_quote


def _f(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _prefix() -> str:
    if (os.environ.get("TEST_RUN") or "").strip().lower() in ("1", "true", "yes"):
        return "test/"
    return ""


def _today_pt() -> str:
    tz = os.environ.get("SCHEDULE_LOCAL_TZ", "America/Los_Angeles")
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def _return_pct(*, direction: str, entry: float, exit_px: float) -> float:
    if entry <= 0:
        return 0.0
    raw = (exit_px - entry) / entry * 100.0
    return raw if direction == "long" else -raw


def score_pick(
    *,
    pick: dict,
    finnhub_key: str,
    today: str,
    allow_test_quote: bool,
) -> dict:
    issue = pick.get("issue_date") or ""
    ticker = pick.get("ticker") or ""
    direction = pick.get("direction") or "long"
    updates: dict = {}

    if not issue or not ticker or issue > today:
        return updates

    entry_status = pick.get("entry_status") or "pending_close"
    entry_price = _f(pick.get("entry_price"))

    # Entry = same-day close on issue_date (only after that calendar day has passed)
    if entry_status == "pending_close" and issue < today:
        close = fetch_daily_close(finnhub_key=finnhub_key, symbol=ticker, trade_date=issue)
        if close is not None:
            updates["entry_price"] = close
            updates["entry_status"] = "open"
            updates["entry_scored_at"] = today
            entry_price = close
            entry_status = "open"
    elif entry_status == "pending_close" and issue == today and allow_test_quote:
        q = fetch_latest_quote(finnhub_key=finnhub_key, symbol=ticker)
        if q is not None:
            updates["entry_price"] = q
            updates["entry_status"] = "test_quote"
            updates["entry_note"] = "TEST: latest quote, not official close"
            entry_price = q
            entry_status = "test_quote"

    if entry_price is None:
        return updates

    # T+7 calendar
    exit_7_date = calendar_add(issue, 7)
    if exit_7_date <= today and not pick.get("exit_7d_price"):
        px = fetch_daily_close(finnhub_key=finnhub_key, symbol=ticker, trade_date=exit_7_date)
        if px is not None:
            updates["exit_7d_date"] = exit_7_date
            updates["exit_7d_price"] = px
            updates["return_7d"] = round(
                _return_pct(direction=direction, entry=entry_price, exit_px=float(px)), 4
            )

    # T+30 calendar
    exit_30_date = calendar_add(issue, 30)
    if exit_30_date <= today and not pick.get("exit_30d_price"):
        px = fetch_daily_close(finnhub_key=finnhub_key, symbol=ticker, trade_date=exit_30_date)
        if px is not None:
            updates["exit_30d_date"] = exit_30_date
            updates["exit_30d_price"] = px
            updates["return_30d"] = round(
                _return_pct(direction=direction, entry=entry_price, exit_px=float(px)), 4
            )
            if entry_status in ("open", "test_quote"):
                updates["entry_status"] = "closed_30"

    return updates
