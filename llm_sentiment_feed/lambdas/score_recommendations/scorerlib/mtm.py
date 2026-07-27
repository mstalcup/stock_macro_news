from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from .prices import calendar_add, fetch_latest_quote, fetch_yahoo_daily_close


def _f(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _ret_pct(direction: str, entry: float, exit_px: float) -> float:
    raw = (exit_px - entry) / entry * 100.0
    return raw if (direction or "long").lower() == "long" else -raw


def build_mtm_report(*, picks: list[dict], today: str, finnhub_key: str) -> dict:
    official_7 = sum(1 for p in picks if p.get("return_7d") is not None)
    official_30 = sum(1 for p in picks if p.get("return_30d") is not None)
    with_entry = sum(1 for p in picks if p.get("entry_price") is not None)
    pending_entry = len(picks) - with_entry

    rows: list[dict] = []
    for p in picks:
        entry = _f(p.get("entry_price"))
        if entry is None:
            continue
        issue = str(p.get("issue_date") or "")
        ticker = str(p.get("ticker") or "")
        direction = str(p.get("direction") or "long")
        model = str(p.get("model_id") or "")
        exit7 = calendar_add(issue, 7)

        if p.get("return_7d") is not None:
            px = _f(p.get("exit_7d_price"))
            label = "t7_official"
        elif exit7 <= today:
            px = fetch_yahoo_daily_close(symbol=ticker, trade_date=exit7)
            label = "t7_close"
        else:
            px = fetch_latest_quote(finnhub_key=finnhub_key, symbol=ticker)
            label = "mtm_latest"

        if px is None:
            continue
        rows.append(
            {
                "issue_date": issue,
                "model_id": model,
                "ticker": ticker,
                "direction": direction,
                "return_pct": round(_ret_pct(direction, entry, px), 4),
                "price_label": label,
            }
        )

    def _agg(items: list[float]) -> dict:
        if not items:
            return {"n": 0, "avg_return_pct": None, "wins": 0, "win_rate_pct": None}
        wins = sum(1 for r in items if r > 0)
        return {
            "n": len(items),
            "avg_return_pct": round(sum(items) / len(items), 4),
            "wins": wins,
            "win_rate_pct": round(100.0 * wins / len(items), 1),
        }

    by_model: dict[str, list[float]] = defaultdict(list)
    by_issue: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_model[r["model_id"]].append(r["return_pct"])
        by_issue[r["issue_date"]].append(r["return_pct"])

    all_rets = [r["return_pct"] for r in rows]
    top = sorted(rows, key=lambda x: x["return_pct"], reverse=True)[:5]
    bottom = sorted(rows, key=lambda x: x["return_pct"])[:5]

    return {
        "today_pt": today,
        "pick_count": len(picks),
        "with_entry": with_entry,
        "pending_entry": pending_entry,
        "official_t7_count": official_7,
        "official_t30_count": official_30,
        "priced_for_mtm": len(rows),
        "note": "T+7 official exits fill on calendar T+7; earlier cohorts use latest quote until then.",
        "all_models": _agg(all_rets),
        "by_model": {m: _agg(rs) for m, rs in sorted(by_model.items())},
        "by_issue_date": {d: _agg(rs) for d, rs in sorted(by_issue.items())},
        "top_picks": top,
        "bottom_picks": bottom,
    }
