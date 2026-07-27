"""Running ledger of anonymous large options flow (open / reduce / close)."""
from __future__ import annotations

import hashlib
from datetime import date

from .options_flow_config import CLOSE_FRACTION_OPPOSITE_FLOW


def position_id(underlying: str, option_type: str, strike: float, expiry: str) -> str:
    raw = f"{underlying.upper()}|{option_type}|{strike}|{expiry}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _oi_cache_key(ev: dict) -> str:
    return position_id(
        ev.get("underlying") or "",
        ev.get("option_type") or "",
        float(ev.get("strike") or 0),
        ev.get("expiry") or "",
    )


def apply_events(
    ledger: dict,
    events: list[dict],
    *,
    oi_cache: dict[str, int] | None = None,
) -> tuple[dict, list[dict]]:
    """
    Update ledger from today's flow events.
    Uses OI delta (if prior cache) to guess open vs close; else treats large flow as additive.
    """
    oi_cache = oi_cache or {}
    positions: dict[str, dict] = {p["position_id"]: p for p in ledger.get("positions") or []}
    alerts: list[dict] = []

    for ev in events:
        pid = _oi_cache_key(ev)
        oi = int(ev.get("open_interest") or 0)
        prev_oi = oi_cache.get(pid)
        oi_delta = (oi - prev_oi) if prev_oi is not None else None
        oi_cache[pid] = oi

        prem = float(ev.get("est_premium_usd") or 0)
        opt_type = ev.get("option_type") or ""
        underlying = (ev.get("underlying") or "").upper()
        bearish = opt_type == "put"

        # Opening: OI up or new position
        opening = oi_delta is None or oi_delta > 0
        closing = oi_delta is not None and oi_delta < 0

        if opening and prem > 0:
            if pid not in positions:
                positions[pid] = {
                    "position_id": pid,
                    "underlying": underlying,
                    "option_type": opt_type,
                    "strike": ev.get("strike"),
                    "expiry": ev.get("expiry"),
                    "bias": "bearish" if bearish else "bullish",
                    "status": "open",
                    "opened_at": ev.get("trade_date") or date.today().isoformat(),
                    "last_seen": ev.get("trade_date") or "",
                    "est_premium_usd": round(prem, 2),
                    "contracts_est": int(ev.get("volume") or 0),
                    "event_count": 1,
                }
                alerts.append({"action": "open", "position": positions[pid], "event": ev})
            else:
                p = positions[pid]
                if p.get("status") == "open":
                    p["est_premium_usd"] = round(float(p.get("est_premium_usd") or 0) + prem, 2)
                    p["contracts_est"] = int(p.get("contracts_est") or 0) + int(ev.get("volume") or 0)
                    p["last_seen"] = ev.get("trade_date") or ""
                    p["event_count"] = int(p.get("event_count") or 0) + 1
                    alerts.append({"action": "add", "position": p, "event": ev})

        if closing and pid in positions and positions[pid].get("status") == "open":
            p = positions[pid]
            open_est = float(p.get("est_premium_usd") or 1)
            if prem >= open_est * CLOSE_FRACTION_OPPOSITE_FLOW:
                p["status"] = "closed"
                p["closed_at"] = ev.get("trade_date") or ""
                p["close_reason"] = "oi_down_large_flow"
                alerts.append({"action": "close", "position": p, "event": ev})
            else:
                p["status"] = "reduced"
                p["est_premium_usd"] = round(max(0, open_est - prem), 2)
                p["last_seen"] = ev.get("trade_date") or ""
                alerts.append({"action": "reduce", "position": p, "event": ev})

    # Expire stale
    today = date.today().isoformat()
    for p in positions.values():
        if p.get("status") == "open" and (p.get("expiry") or "") < today:
            p["status"] = "expired"
            p["closed_at"] = today

    ledger["positions"] = sorted(
        positions.values(),
        key=lambda x: float(x.get("est_premium_usd") or 0),
        reverse=True,
    )
    ledger["open_count"] = sum(1 for p in ledger["positions"] if p.get("status") == "open")
    return ledger, alerts


def sector_rollups(events: list[dict]) -> dict:
    """Aggregate anonymous flow for weekly report."""
    by_underlying: dict[str, float] = {}
    put_prem = 0.0
    call_prem = 0.0
    for ev in events:
        u = (ev.get("underlying") or "").upper()
        p = float(ev.get("est_premium_usd") or 0)
        by_underlying[u] = by_underlying.get(u, 0) + p
        if (ev.get("option_type") or "") == "put":
            put_prem += p
        else:
            call_prem += p
    top = sorted(by_underlying.items(), key=lambda x: x[1], reverse=True)[:25]
    return {
        "put_premium_usd": round(put_prem, 2),
        "call_premium_usd": round(call_prem, 2),
        "put_share_pct": round(100 * put_prem / (put_prem + call_prem), 1) if put_prem + call_prem else 0,
        "top_underlyings": [{"ticker": t, "premium_usd": round(v, 2)} for t, v in top],
        "event_count": len(events),
    }
