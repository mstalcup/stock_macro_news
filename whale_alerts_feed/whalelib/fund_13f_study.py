"""Deep 13F study for a fund — puts, longs, quarter diffs (not just 13D/G)."""
from __future__ import annotations

from .edgar import build_name_ticker_map, recent_filings_for_cik
from .thirteen_f import parse_holdings, position_side


def _holding_key(h: dict) -> tuple[str, str, str]:
    return (
        (h.get("issuer_name") or "").upper(),
        (h.get("cusip") or "").upper(),
        h.get("side") or "long",
    )


def study_fund_13f_history(
    cik: str,
    *,
    fund_name: str = "",
    max_filings: int = 4,
) -> dict:
    """Parse recent 13F filings and diff puts/long stacks quarter over quarter."""
    filings = recent_filings_for_cik(cik, forms={"13F-HR", "13F-HR/A"}, limit=max_filings)
    name_map = build_name_ticker_map()
    quarters: list[dict] = []
    for f in filings:
        acc = f.get("accession_dashed") or f.get("accession") or ""
        holdings = parse_holdings(str(cik).lstrip("0"), acc, name_map=name_map)
        if not holdings:
            continue
        longs = [h for h in holdings if h.get("side") == "long"]
        puts = [h for h in holdings if h.get("side") == "put"]
        calls = [h for h in holdings if h.get("side") == "call"]
        total = sum(h["value_usd"] for h in holdings)
        put_notional = sum(h["value_usd"] for h in puts)
        quarters.append(
            {
                "filing_date": f.get("file_date") or "",
                "accession": acc,
                "holdings_count": len(holdings),
                "long_count": len(longs),
                "put_count": len(puts),
                "call_count": len(calls),
                "total_value_usd": round(total, 2),
                "put_notional_usd": round(put_notional, 2),
                "put_pct_of_book": round(100.0 * put_notional / total, 2) if total else 0.0,
                "top_puts": sorted(puts, key=lambda x: x["value_usd"], reverse=True)[:15],
                "top_longs": sorted(longs, key=lambda x: x["value_usd"], reverse=True)[:15],
                "all_holdings": holdings,
            }
        )

    diffs: list[dict] = []
    for i in range(len(quarters) - 1):
        curr, prev = quarters[i], quarters[i + 1]
        prev_puts = {_holding_key(h): h for h in prev["all_holdings"] if h.get("side") == "put"}
        curr_puts = {_holding_key(h): h for h in curr["all_holdings"] if h.get("side") == "put"}
        new_puts = [curr_puts[k] for k in curr_puts if k not in prev_puts]
        exited_puts = [prev_puts[k] for k in prev_puts if k not in curr_puts]
        increased_puts = []
        for k, ch in curr_puts.items():
            if k in prev_puts and ch["value_usd"] > prev_puts[k]["value_usd"] * 1.1:
                increased_puts.append(
                    {
                        **ch,
                        "prev_value_usd": prev_puts[k]["value_usd"],
                        "delta_usd": round(ch["value_usd"] - prev_puts[k]["value_usd"], 2),
                    }
                )
        diffs.append(
            {
                "from_filing": prev["filing_date"],
                "to_filing": curr["filing_date"],
                "put_notional_change_usd": round(curr["put_notional_usd"] - prev["put_notional_usd"], 2),
                "new_puts": sorted(new_puts, key=lambda x: x["value_usd"], reverse=True),
                "exited_puts": sorted(exited_puts, key=lambda x: x["value_usd"], reverse=True),
                "increased_puts": sorted(increased_puts, key=lambda x: x["value_usd"], reverse=True),
            }
        )

    chip_keywords = (
        "NVIDIA", "BROADCOM", "ADVANCED MICRO", "MICRON", "INTEL", "ASML",
        "TAIWAN", "SEMICONDUCT", "VANECK", "ORACLE", "CORNING", "LAM RESEARCH",
        "APPLIED MAT", "KLA", "QUALCOMM",
    )

    latest = quarters[0] if quarters else {}
    chip_puts = []
    if latest:
        for p in latest.get("all_holdings", []):
            if p.get("side") != "put":
                continue
            name = (p.get("issuer_name") or "").upper()
            if any(k in name for k in chip_keywords):
                chip_puts.append(p)

    return {
        "fund_name": fund_name,
        "filer_cik": str(cik).lstrip("0"),
        "quarters": quarters,
        "quarter_diffs": diffs,
        "latest_chip_puts": sorted(chip_puts, key=lambda x: x["value_usd"], reverse=True),
        "schedule_filing_note": (
            "13D/G only fires when a stake crosses 5% — most SA chip exposure is "
            "13F puts/options and sub-5% equity; schedule layer misses the book."
        ),
    }
