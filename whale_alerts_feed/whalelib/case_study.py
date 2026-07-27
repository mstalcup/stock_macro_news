"""Backward case study — what could have fired before 13F for a single fund."""
from __future__ import annotations

from datetime import date, timedelta

from .backtest import compute_forward_alphas
from .edgar import (
    build_name_ticker_map,
    company_tickers_map,
    enrich_schedule_filing,
    parse_schedule_entities,
)
from .eight_k import eight_k_signals_from_schedule
from .types import WhaleSignal
from .volume_signals import detect_volume_spike


def _hits_to_rows(hits: list[dict]) -> list[dict]:
    rows: list[dict] = []
    for h in hits:
        p = parse_schedule_entities(h)
        ticker = (p.get("issuer_ticker") or "").upper()
        st = p.get("signal_type") or ""
        if not ticker or st not in ("13d_new", "13d_increase", "13g_new", "13g_increase"):
            continue
        rows.append(
            {
                "ticker": ticker,
                "signal_type": st,
                "signal_date": p.get("file_date") or "",
                "filer_cik": str(p.get("filer_cik") or "").lstrip("0"),
                "filer_name": p.get("filer_name") or "",
                "accession": p.get("accession_dashed") or p.get("accession") or "",
                "form": p.get("form") or "",
            }
        )
    return rows


def _earliest_volume_spike(ticker: str, *, before: str, lookback_days: int = 60) -> dict | None:
    try:
        end = date.fromisoformat(before)
    except ValueError:
        return None
    best: dict | None = None
    for offset in range(lookback_days, 0, -1):
        scan = (end - timedelta(days=offset)).isoformat()
        spike = detect_volume_spike(ticker, as_of=scan)
        if spike:
            best = {**spike, "days_before_filing": offset}
            break
    return best


def _first_filing_per_ticker(rows: list[dict]) -> dict[str, dict]:
    by_ticker: dict[str, dict] = {}
    for r in sorted(rows, key=lambda x: x.get("signal_date") or ""):
        t = r["ticker"]
        if t not in by_ticker:
            by_ticker[t] = r
    return by_ticker


def build_fund_case_study(
    *,
    fund_ciks: list[str],
    fund_name: str,
    schedule_hits: list[dict],
    funds_meta: list[dict],
    volume_lookback: int = 60,
    eight_k_lookback: int = 45,
) -> dict:
    """
    Start from known filings, scan backward for volume / 8-K / filing-type patterns.
    """
    cik_set = {str(c).lstrip("0") for c in fund_ciks}
    fund_hits = [h for h in schedule_hits if str(h.get("filer_cik", "")).lstrip("0") in cik_set]
    rows = _hits_to_rows(fund_hits)
    first_by_ticker = _first_filing_per_ticker(rows)

    eight_k = eight_k_signals_from_schedule(rows, funds_meta, lookback_days=eight_k_lookback)
    eight_k_idx = {(s.ticker.upper(), s.filer_cik): s for s in eight_k}

    positions: list[dict] = []
    hypotheses: dict[str, list[dict]] = {
        "13g_first": [],
        "13d_first": [],
        "volume_early": [],
        "8k_before_schedule": [],
        "schedule_only": [],
    }

    for ticker, first in first_by_ticker.items():
        filing_date = first["signal_date"]
        vol = _earliest_volume_spike(ticker, before=filing_date, lookback_days=volume_lookback)
        ek = eight_k_idx.get((ticker, first["filer_cik"]))
        all_filings = [r for r in rows if r["ticker"] == ticker]
        all_filings.sort(key=lambda x: x["signal_date"])

        lead_volume = None
        if vol and filing_date:
            try:
                lead_volume = (date.fromisoformat(filing_date) - date.fromisoformat(vol["signal_date"])).days
            except ValueError:
                pass

        lead_8k = None
        if ek and filing_date:
            try:
                lead_8k = (date.fromisoformat(filing_date) - date.fromisoformat(ek.signal_date)).days
            except ValueError:
                pass

        earliest_detectable = None
        detectable_type = None
        if vol and (lead_8k is None or (lead_volume is not None and lead_volume >= (lead_8k or 0))):
            earliest_detectable = vol["signal_date"]
            detectable_type = "volume_spike"
        elif ek:
            earliest_detectable = ek.signal_date
            detectable_type = "8k_stake"

        pos = {
            "ticker": ticker,
            "first_filing_type": first["signal_type"],
            "first_filing_date": filing_date,
            "filing_count": len(all_filings),
            "all_filings": all_filings,
            "volume_spike": vol,
            "volume_lead_days": lead_volume,
            "eight_k": (
                {
                    "signal_date": ek.signal_date,
                    "signal_id": ek.signal_id,
                    "lead_days": lead_8k,
                }
                if ek
                else None
            ),
            "earliest_detectable": earliest_detectable,
            "earliest_detectable_type": detectable_type,
        }
        positions.append(pos)

        bucket = "schedule_only"
        if first["signal_type"].startswith("13g"):
            bucket = "13g_first"
        elif first["signal_type"].startswith("13d"):
            bucket = "13d_first"
        if vol and lead_volume and lead_volume >= 1:
            hypotheses["volume_early"].append(pos)
        if ek:
            hypotheses["8k_before_schedule"].append(pos)
        hypotheses[bucket].append(pos)

    # Score hypothetical early signals
    early_sigs: list[WhaleSignal] = []
    for p in positions:
        if not p.get("earliest_detectable"):
            continue
        early_sigs.append(
            WhaleSignal(
                signal_id=f"early#{p['ticker']}#{p['earliest_detectable']}",
                signal_type=p["earliest_detectable_type"] or "unknown",
                signal_date=p["earliest_detectable"],
                filer_cik=first_by_ticker[p["ticker"]]["filer_cik"],
                filer_name=fund_name,
                ticker=p["ticker"],
                alert_class="case_study",
            )
        )
    schedule_sigs = [
        WhaleSignal(
            signal_id=f"sched#{p['ticker']}#{p['first_filing_date']}",
            signal_type=p["first_filing_type"],
            signal_date=p["first_filing_date"],
            filer_cik=first_by_ticker[p["ticker"]]["filer_cik"],
            filer_name=fund_name,
            ticker=p["ticker"],
            alert_class="case_study",
        )
        for p in positions
    ]
    scored_early = compute_forward_alphas(early_sigs)
    scored_sched = compute_forward_alphas(schedule_sigs)

    return {
        "fund_name": fund_name,
        "fund_ciks": sorted(cik_set),
        "filing_rows": len(rows),
        "unique_tickers": len(positions),
        "positions": positions,
        "hypothesis_buckets": {k: len(v) for k, v in hypotheses.items()},
        "scored_early_signals": scored_early,
        "scored_schedule_signals": scored_sched,
    }


def enrich_hits_for_ciks(ciks: list[str], *, years: int = 2) -> list[dict]:
    from .edgar import all_schedule_filings_for_cik

    cik_map = company_tickers_map()
    name_map = build_name_ticker_map()
    hits: list[dict] = []
    for cik in ciks:
        batch = all_schedule_filings_for_cik(cik, years=years)
        for h in batch:
            hits.append(enrich_schedule_filing(h, cik_map, name_map))
    return hits
