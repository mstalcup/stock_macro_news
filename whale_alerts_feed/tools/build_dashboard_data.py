"""Build static JSON payload for the 13F HTML dashboard."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whalelib.backtest import save_json
from whalelib.edgar import build_name_ticker_map
from whalelib.thirteen_f import aggregate_whale_index, fund_portfolio_snapshot

OUT = ROOT / "output"
DASH = OUT / "dashboard"
ROSTER = ROOT / "seed" / "fund_roster_curated.json"
CACHE = OUT / "cache"
MAX_FUND_TOTAL_USD = 500e9  # skip absurd 13F value parses from polluting rollup


def _write_dashboard_js(payload: dict) -> None:
    """Separate JS file loads before app — works on file:// unlike fetch()."""
    DASH.mkdir(parents=True, exist_ok=True)
    blob = json.dumps(payload, ensure_ascii=False)
    js_path = DASH / "dashboard_data.js"
    js_path.write_text(f"window.__DASHBOARD_DATA__ = {blob};\n", encoding="utf-8")
    print(f"Wrote {js_path}")


def _position_label(h: dict) -> str:
    ticker = (h.get("ticker") or "").strip()
    if ticker:
        return ticker
    issuer = (h.get("issuer_name") or "").strip()
    title = (h.get("title_of_class") or "").strip()
    if issuer and title:
        return f"{issuer} · {title}"
    return issuer or title or (h.get("cusip") or "?")


def _position_key(h: dict) -> str:
    side = h.get("side") or "long"
    ticker = (h.get("ticker") or "").strip().upper()
    if ticker:
        return f"{ticker}|{side}"
    issuer = (h.get("issuer_name") or "").strip()
    title = (h.get("title_of_class") or "").strip()
    slug = f"{issuer}|{title}" if title else issuer
    slug = slug[:48] or (h.get("cusip") or "?")
    return f"{slug}|{side}"


def _build_side_rollups(snapshots: list[dict]) -> tuple[dict[str, dict], dict[str, list[dict]]]:
    """Per-side positions with per-fund breakdown for drill-down charts."""
    details: dict[str, dict] = {}

    for s in snapshots:
        if not s or not s.get("holdings"):
            continue
        if (s.get("total_value_usd") or 0) > MAX_FUND_TOTAL_USD:
            continue
        fund_name = s.get("fund_name") or s.get("filer_name") or s["filer_cik"]
        tier = s.get("tier") or ""
        cik = s["filer_cik"]
        for h in s["holdings"]:
            value = h.get("value_usd") or 0
            if value <= 0:
                continue
            pk = _position_key(h)
            if pk not in details:
                details[pk] = {
                    "pos_id": pk,
                    "side": h.get("side") or "long",
                    "ticker": (h.get("ticker") or "").strip(),
                    "label": _position_label(h),
                    "issuer_name": (h.get("issuer_name") or "").strip(),
                    "title_of_class": (h.get("title_of_class") or "").strip(),
                    "value_usd": 0.0,
                    "funds": [],
                }
            details[pk]["value_usd"] += value
            details[pk]["funds"].append(
                {
                    "cik": cik,
                    "name": fund_name,
                    "tier": tier,
                    "value_usd": round(value, 2),
                    "weight_in_fund_pct": h.get("weight_pct"),
                    "shares": h.get("shares") or "",
                    "shares_type": h.get("shares_type") or "",
                }
            )

    by_side: dict[str, list[dict]] = defaultdict(list)
    for d in details.values():
        total = d["value_usd"] or 1
        merged: dict[str, dict] = {}
        for f in d["funds"]:
            prev = merged.get(f["cik"])
            if prev:
                prev["value_usd"] = round(prev["value_usd"] + f["value_usd"], 2)
            else:
                merged[f["cik"]] = dict(f)
        unique_funds = sorted(merged.values(), key=lambda x: x["value_usd"], reverse=True)
        for f in unique_funds:
            f["share_of_position_pct"] = round(100 * f["value_usd"] / total, 2)
        d["funds"] = unique_funds
        d["fund_count"] = len(unique_funds)
        d["value_usd"] = round(d["value_usd"], 2)
        by_side[d["side"]].append(d)

    rolled: dict[str, list[dict]] = {}
    for side, rows in by_side.items():
        book_total = sum(r["value_usd"] for r in rows) or 1
        rows.sort(key=lambda x: x["value_usd"], reverse=True)
        rolled[side] = [
            {
                "pos_id": r["pos_id"],
                "ticker": r["ticker"] or r["label"][:24],
                "label": r["label"],
                "side": r["side"],
                "value_usd": r["value_usd"],
                "weight_pct": round(100 * r["value_usd"] / book_total, 4),
                "fund_count": r["fund_count"],
            }
            for r in rows[:40]
        ]

    return details, rolled


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--refresh", action="store_true", help="Re-fetch 13Fs from SEC (slow)")
    args = ap.parse_args()

    roster = json.loads(ROSTER.read_text(encoding="utf-8"))
    f13_hits = []
    f13_path = CACHE / "f13_hits.json"
    if f13_path.is_file():
        f13_hits = json.loads(f13_path.read_text(encoding="utf-8"))

    snapshots_path = OUT / "whale_13f_snapshots.json"
    if args.refresh or not snapshots_path.is_file():
        name_map = build_name_ticker_map()
        snapshots = []
        for f in roster:
            cik = str(f.get("cik") or "").lstrip("0")
            snap = fund_portfolio_snapshot(cik, f13_hits=f13_hits or None, name_map=name_map)
            if snap:
                snap["fund_name"] = f.get("fund_name") or ""
                snap["tier"] = f.get("tier") or ""
                snapshots.append(snap)
        save_json(snapshots_path, snapshots)
    else:
        snapshots = json.loads(snapshots_path.read_text(encoding="utf-8"))

    index = aggregate_whale_index(snapshots)
    position_details, rolled_by_side = _build_side_rollups(snapshots)

    # Slim fund cards for dashboard
    fund_cards = []
    for s in snapshots:
        if not s or not s.get("holdings"):
            continue
        if (s.get("total_value_usd") or 0) > MAX_FUND_TOTAL_USD:
            continue
        holdings = s["holdings"]
        longs = [h for h in holdings if h.get("side", "long") == "long"]
        puts = [h for h in holdings if h.get("side") == "put"]
        calls = [h for h in holdings if h.get("side") == "call"]
        fund_cards.append(
            {
                "cik": s["filer_cik"],
                "name": s.get("fund_name") or s.get("filer_name") or s["filer_cik"],
                "tier": s.get("tier") or "",
                "filing_date": s.get("filing_date") or "",
                "period_ending": s.get("period_ending") or "",
                "total_value_usd": s.get("total_value_usd") or 0,
                "put_notional_usd": round(sum(h["value_usd"] for h in puts), 2),
                "call_notional_usd": round(sum(h["value_usd"] for h in calls), 2),
                "long_notional_usd": round(sum(h["value_usd"] for h in longs), 2),
                "top_longs": sorted(longs, key=lambda x: x["value_usd"], reverse=True)[:12],
                "top_puts": sorted(puts, key=lambda x: x["value_usd"], reverse=True)[:12],
                "top_calls": sorted(calls, key=lambda x: x["value_usd"], reverse=True)[:12],
            }
        )
    fund_cards.sort(key=lambda x: x["total_value_usd"], reverse=True)

    # Latest quarter label across funds
    periods = sorted({c["period_ending"] for c in fund_cards if c.get("period_ending")}, reverse=True)
    latest_quarter = periods[0] if periods else ""

    positions = index.get("positions") or []
    payload = {
        "generated_at": date.today().isoformat(),
        "latest_quarter": latest_quarter,
        "fund_count": len(fund_cards),
        "aggregate_aum_usd": sum(c["total_value_usd"] for c in fund_cards),
        "position_count": len(positions),
        "funds": fund_cards,
        "index_positions": positions[:50],
        "rolled_up_longs": rolled_by_side.get("long", []),
        "rolled_up_puts": rolled_by_side.get("put", []),
        "rolled_up_calls": rolled_by_side.get("call", []),
        "position_details": position_details,
    }

    DASH.mkdir(parents=True, exist_ok=True)
    out_path = DASH / "whale_13f_dashboard_data.json"
    save_json(out_path, payload)
    print(f"Wrote {out_path} ({len(fund_cards)} funds)")
    _write_dashboard_js(payload)


if __name__ == "__main__":
    main()
