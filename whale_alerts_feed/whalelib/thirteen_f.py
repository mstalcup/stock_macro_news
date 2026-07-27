"""13F holdings parse, aggregate portfolio, and roster rotation helpers."""
from __future__ import annotations

from collections import defaultdict

from .edgar import (
    build_name_ticker_map,
    fetch_13f_holdings,
    issuer_ticker_from_13f_row,
    normalize_accession,
    recent_filings_for_cik,
)


def _shares_count(row: dict) -> float:
    raw = row.get("sshPrnamt") or row.get("sshprnamt") or ""
    try:
        return float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0


def holding_value_usd(row: dict) -> float:
    """SEC 13F `value` field is in thousands of USD; some filers over-scale — fix via share sanity."""
    raw = row.get("value") or row.get("Value") or 0
    try:
        val = float(str(raw).replace(",", ""))
    except (TypeError, ValueError):
        return 0.0
    if val <= 0:
        return 0.0
    shares = _shares_count(row)
    if shares > 0:
        # Implied price/share; if absurd, filer likely padded value — scale down.
        while val > 0 and (val * 1000.0 / shares) > 5000.0:
            val /= 1000.0
    usd = val * 1000.0
    if usd > 500e9:
        return 0.0
    return usd


def position_side(row: dict) -> str:
    """Equity long, put, or call from 13F putCall / titleOfClass."""
    put_call = (row.get("putCall") or row.get("putcall") or "").strip().lower()
    if put_call == "put":
        return "put"
    if put_call == "call":
        return "call"
    title = (row.get("titleOfClass") or "").upper()
    if "PUT" in title:
        return "put"
    if "CALL" in title:
        return "call"
    return "long"


def parse_holdings(cik: str, accession: str, *, name_map: dict[str, str] | None = None) -> list[dict]:
    nm = name_map or build_name_ticker_map()
    rows = fetch_13f_holdings(cik, accession)
    out: list[dict] = []
    for row in rows:
        cusip = (row.get("cusip") or "").strip()
        if not cusip:
            continue
        ticker = issuer_ticker_from_13f_row(row, nm)
        value = holding_value_usd(row)
        if value <= 0:
            continue
        side = position_side(row)
        out.append(
            {
                "ticker": ticker.upper() if ticker else "",
                "issuer_name": (row.get("nameOfIssuer") or row.get("nameofissuer") or "").strip(),
                "title_of_class": (row.get("titleOfClass") or "").strip(),
                "value_usd": round(value, 2),
                "shares": row.get("sshPrnamt") or row.get("sshprnamt") or "",
                "shares_type": row.get("sshPrnamtType") or row.get("sshprnamttype") or "",
                "side": side,
                "cusip": row.get("cusip") or "",
            }
        )
    return out


def latest_13f_filing(cik: str) -> dict | None:
    hits = recent_filings_for_cik(cik, forms={"13F-HR", "13F-HR/A"}, limit=5)
    if not hits:
        return None
    return hits[0]


def latest_13f_from_cache(cik: str, f13_hits: list[dict]) -> dict | None:
    cik = str(cik).lstrip("0")
    matches = [h for h in f13_hits if str(h.get("filer_cik", "")).lstrip("0") == cik]
    if not matches:
        return None
    matches.sort(key=lambda x: x.get("file_date") or "", reverse=True)
    return matches[0]


def fund_portfolio_snapshot(
    cik: str,
    *,
    f13_hits: list[dict] | None = None,
    name_map: dict[str, str] | None = None,
) -> dict | None:
    filing = None
    if f13_hits:
        filing = latest_13f_from_cache(cik, f13_hits)
    if not filing:
        filing = latest_13f_filing(cik)
    if not filing:
        return None
    acc = filing.get("accession_dashed") or filing.get("accession") or ""
    if acc and ":" not in acc:
        _, dashed = normalize_accession(acc)
        acc = dashed
    holdings = parse_holdings(str(cik).lstrip("0"), acc, name_map=name_map)
    total = sum(h["value_usd"] for h in holdings if h["value_usd"] > 0)
    for h in holdings:
        h["weight_pct"] = round(100.0 * h["value_usd"] / total, 4) if total > 0 else 0.0
    holdings.sort(key=lambda x: x["value_usd"], reverse=True)
    if len(holdings) > 2000:
        return None
    return {
        "filer_cik": str(cik).lstrip("0"),
        "filer_name": filing.get("filer_name") or "",
        "filing_date": filing.get("file_date") or "",
        "period_ending": filing.get("period_ending") or "",
        "accession": acc,
        "total_value_usd": round(total, 2),
        "holdings_count": len(holdings),
        "holdings": holdings,
    }


def aggregate_whale_index(snapshots: list[dict]) -> dict:
    """Roll up latest 13F snapshots into a synthetic ETF-style weight table."""
    by_ticker: dict[str, dict] = defaultdict(
        lambda: {"value_usd": 0.0, "funds": [], "issuer_name": ""}
    )
    fund_totals = 0.0
    contributing = 0
    for snap in snapshots:
        if not snap or not snap.get("holdings"):
            continue
        contributing += 1
        fund_totals += snap.get("total_value_usd") or 0
        for h in snap["holdings"]:
            t = h.get("ticker") or ""
            if not t:
                continue
            by_ticker[t]["value_usd"] += h["value_usd"]
            by_ticker[t]["issuer_name"] = h.get("issuer_name") or by_ticker[t]["issuer_name"]
            by_ticker[t]["funds"].append(
                {
                    "cik": snap["filer_cik"],
                    "name": snap.get("filer_name") or "",
                    "weight_in_fund_pct": h.get("weight_pct"),
                    "value_usd": h["value_usd"],
                }
            )

    total = sum(v["value_usd"] for v in by_ticker.values())
    positions = []
    for ticker, row in by_ticker.items():
        positions.append(
            {
                "ticker": ticker,
                "issuer_name": row["issuer_name"],
                "value_usd": round(row["value_usd"], 2),
                "weight_pct": round(100.0 * row["value_usd"] / total, 4) if total > 0 else 0.0,
                "fund_count": len(row["funds"]),
                "funds": row["funds"],
            }
        )
    positions.sort(key=lambda x: x["weight_pct"], reverse=True)
    return {
        "contributing_funds": contributing,
        "aggregate_aum_usd": round(fund_totals, 2),
        "rolled_up_value_usd": round(total, 2),
        "position_count": len(positions),
        "positions": positions,
    }


def suggest_tier_rotation(
    rankings: list[dict],
    roster: list[dict],
    *,
    top_s: int = 5,
    top_a: int = 12,
) -> list[dict]:
    """S&P-style tier suggestions from backtest composite scores."""
    ranked = [r for r in rankings if r.get("status") == "ranked"]
    ranked.sort(key=lambda x: x.get("composite_score") or -999, reverse=True)
    tier_by_cik: dict[str, str] = {}
    for i, r in enumerate(ranked):
        cik = str(r.get("filer_cik") or "").lstrip("0")
        if not cik:
            continue
        if i < top_s:
            tier_by_cik[cik] = "S"
        elif i < top_a:
            tier_by_cik[cik] = "A"
        else:
            tier_by_cik[cik] = "B"

    out: list[dict] = []
    for f in roster:
        cik = str(f.get("cik") or "").lstrip("0")
        current = f.get("tier") or "B"
        suggested = tier_by_cik.get(cik, "C")
        r = next((x for x in ranked if str(x.get("filer_cik", "")).lstrip("0") == cik), None)
        out.append(
            {
                "cik": cik,
                "fund_name": f.get("fund_name") or "",
                "current_tier": current,
                "suggested_tier": suggested,
                "tier_change": current != suggested,
                "composite_score": r.get("composite_score") if r else None,
                "median_alpha_20d": r.get("median_alpha_20d") if r else None,
                "signal_count": r.get("signal_count") if r else 0,
                "status": r.get("status") if r else "not_ranked",
            }
        )
    out.sort(key=lambda x: x.get("composite_score") or -999, reverse=True)
    return out
