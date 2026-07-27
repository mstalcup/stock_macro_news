"""Score historical anticipatory signals and rank funds."""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict, Counter
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whalelib.backtest_config import FILING_AGENT_MIN_ISSUERS
from whalelib.backtest import (
    build_roster_from_rankings,
    compute_forward_alphas,
    link_anticipation,
    rank_funds,
    save_json,
    write_backtest_report,
    write_curated_backtest_report,
)
from whalelib.edgar import (
    all_schedule_filings_for_cik,
    build_name_ticker_map,
    company_name_for_cik,
    company_tickers_map,
    enrich_schedule_filing,
    fetch_13f_holdings,
    issuer_ticker_from_13f_row,
    normalize_accession,
    parse_schedule_entities,
)
from whalelib.types import WhaleSignal

CACHE = ROOT / "output" / "cache"
OUT = ROOT / "output"


def _normalize_hit(h: dict) -> dict:
    return parse_schedule_entities(h)


def _hits_to_anticipatory(hits: list[dict]) -> list[WhaleSignal]:
    signals: list[WhaleSignal] = []
    for h in hits:
        ticker = (h.get("issuer_ticker") or "").upper()
        st = h.get("signal_type") or ""
        if st not in ("13d_new", "13d_increase", "13g_new", "13g_increase"):
            continue
        if not ticker:
            continue
        acc = h.get("accession_dashed") or h.get("accession") or ""
        signals.append(
            WhaleSignal(
                signal_id=f"{st}#{acc}#{ticker}",
                signal_type=st,
                signal_date=h.get("file_date") or "",
                filer_cik=str(h.get("filer_cik") or "").lstrip("0"),
                filer_name=h.get("filer_name") or "",
                ticker=ticker,
                issuer_name=h.get("issuer_name") or "",
                accession=acc,
                alert_class="primary",
            )
        )
    return signals


def _build_13f_signals(f13_hits: list[dict], *, max_filings: int = 150) -> list[WhaleSignal]:
    """Diff consecutive 13F per filer CIK — new positions at filing date."""
    name_map = build_name_ticker_map()
    by_cik: dict[str, list[dict]] = defaultdict(list)
    for raw in f13_hits:
        nodash, dashed = normalize_accession(raw.get("accession_dashed") or raw.get("accession") or "")
        h = dict(raw)
        h["accession"] = nodash
        h["accession_dashed"] = dashed
        cik = str(h.get("filer_cik") or "").lstrip("0")
        if cik:
            by_cik[cik].append(h)
    signals: list[WhaleSignal] = []
    processed = 0
    for cik, filings in by_cik.items():
        filings.sort(key=lambda x: x.get("file_date") or "")
        prev_tickers: set[str] = set()
        for f in filings:
            if processed >= max_filings:
                break
            acc = f.get("accession_dashed") or ""
            if not acc:
                continue
            holdings = fetch_13f_holdings(cik, acc)
            processed += 1
            if not holdings:
                continue
            curr: set[str] = set()
            for row in holdings:
                t = issuer_ticker_from_13f_row(row, name_map)
                if t:
                    curr.add(t)
            new_pos = curr - prev_tickers
            qend = f.get("period_ending") or ""
            for t in new_pos:
                signals.append(
                    WhaleSignal(
                        signal_id=f"13f_new#{acc}#{t}",
                        signal_type="13f_new_position",
                        signal_date=f.get("file_date") or "",
                        filer_cik=cik,
                        filer_name=f.get("filer_name") or "",
                        ticker=t,
                        quarter_end=qend,
                        accession=acc,
                        alert_class="confirmation_only",
                    )
                )
            prev_tickers = curr
        if processed >= max_filings:
            break
    return signals


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--score-limit", type=int, default=120, help="Max anticipatory signals to score (Yahoo calls)")
    ap.add_argument("--f13-limit", type=int, default=80, help="Max 13F filings to parse for diff")
    ap.add_argument("--top-roster", type=int, default=50)
    ap.add_argument(
        "--curated-only",
        action="store_true",
        help="Score 13D/G from SEC submissions for seed/fund_roster_curated.json only",
    )
    ap.add_argument("--years", type=float, default=2)
    args = ap.parse_args()

    if args.curated_only:
        _run_curated_backtest(args)
        return

    sched_path = CACHE / "schedule_hits.json"
    f13_path = CACHE / "f13_hits.json"
    if not sched_path.is_file():
        print("Run backfill_edgar.py first")
        raise SystemExit(1)

    schedule_hits = json.loads(sched_path.read_text(encoding="utf-8"))
    f13_hits = json.loads(f13_path.read_text(encoding="utf-8")) if f13_path.is_file() else []

    parsed_hits = [_normalize_hit(h) for h in schedule_hits]
    issuer_sets: dict[str, set[str]] = defaultdict(set)
    for p in parsed_hits:
        if p.get("filer_cik") and p.get("issuer_ticker"):
            issuer_sets[p["filer_cik"]].add(p["issuer_ticker"])
    agent_ciks = {c for c, ticks in issuer_sets.items() if len(ticks) >= FILING_AGENT_MIN_ISSUERS}
    if agent_ciks:
        print(f"Excluding {len(agent_ciks)} filing-agent CIKs (>{FILING_AGENT_MIN_ISSUERS} issuers)")

    valid = [
        h
        for h in parsed_hits
        if h.get("filer_cik") not in agent_ciks
        and h.get("issuer_ticker")
        and h.get("filer_cik")
        and h.get("filer_cik") != h.get("issuer_cik")
    ]
    anticipatory = _hits_to_anticipatory(valid)
    anticipatory.sort(key=lambda s: (0 if s.signal_type == "13d_new" else 1, s.signal_date))
    to_score = anticipatory[: args.score_limit]
    print(f"Anticipatory signals: {len(anticipatory)} total, scoring {len(to_score)}")

    thirteen_f = _build_13f_signals(f13_hits, max_filings=args.f13_limit)
    print(f"13F new-position signals (confirmation track): {len(thirteen_f)}")

    scored_anti = compute_forward_alphas(to_score)
    scored_f13 = compute_forward_alphas(thirteen_f[: min(40, len(thirteen_f))])
    scored = scored_anti + scored_f13

    links = link_anticipation(to_score, thirteen_f)
    rankings = rank_funds(scored_anti, links)
    for r in rankings:
        if r.get("status") == "ranked" and str(r.get("filer_name", "")).startswith("CIK "):
            name = company_name_for_cik(r["filer_cik"])
            if name:
                r["filer_name"] = name

    manual = []
    mo = ROOT / "seed" / "manual_overrides.json"
    if mo.is_file():
        manual = json.loads(mo.read_text(encoding="utf-8"))

    roster = build_roster_from_rankings(rankings, top_n=args.top_roster, manual_overrides=manual)

    OUT.mkdir(parents=True, exist_ok=True)
    save_json(OUT / "fund_rankings.json", rankings)
    save_json(OUT / "scored_signals.json", scored)
    save_json(OUT / "anticipation_links.json", links)
    save_json(ROOT / "seed" / "fund_roster.json", roster)

    if args.write_report:
        write_backtest_report(
            OUT / "backtest_report.md",
            rankings=rankings,
            links=links,
            scored_count=len(scored_anti),
        )
        print(f"Wrote {OUT / 'backtest_report.md'}")

    print(f"Wrote {OUT / 'fund_rankings.json'}")
    print(f"Wrote {ROOT / 'seed' / 'fund_roster.json'} ({len(roster)} funds)")


def _run_curated_backtest(args) -> None:
    curated_path = ROOT / "seed" / "fund_roster_curated.json"
    if not curated_path.is_file():
        print("Missing seed/fund_roster_curated.json")
        raise SystemExit(1)
    funds = json.loads(curated_path.read_text(encoding="utf-8"))
    cik_map = company_tickers_map()
    name_map = build_name_ticker_map()

    print(f"Curated backtest: {len(funds)} funds, ~{args.years}y from SEC submissions")
    hits: list[dict] = []
    for i, f in enumerate(funds):
        cik = str(f.get("cik") or "").lstrip("0")
        if not cik:
            continue
        batch = all_schedule_filings_for_cik(cik, years=int(args.years))
        print(f"  [{i+1}/{len(funds)}] {f.get('fund_name','')[:35]:<35} {len(batch)} filings")
        for h in batch:
            hits.append(enrich_schedule_filing(h, cik_map, name_map))

    anticipatory = _hits_to_anticipatory(hits)
    anticipatory = [s for s in anticipatory if s.ticker and s.filer_cik]
    print(f"Scorable signals: {len(anticipatory)} (fetching Yahoo forward returns...)")

    scored = compute_forward_alphas(anticipatory)
    rankings = rank_funds(scored, [])
    for r in rankings:
        if r.get("status") == "ranked" and str(r.get("filer_name", "")).startswith("CIK "):
            nm = company_name_for_cik(r["filer_cik"])
            if nm:
                r["filer_name"] = nm
        for f in funds:
            if str(f.get("cik", "")).lstrip("0") == str(r.get("filer_cik", "")):
                r["filer_name"] = f.get("fund_name") or r.get("filer_name")

    OUT.mkdir(parents=True, exist_ok=True)
    save_json(OUT / "curated_scored_signals.json", scored)
    save_json(OUT / "curated_fund_rankings.json", rankings)
    if args.write_report:
        write_curated_backtest_report(
            OUT / "curated_backtest_report.md",
            funds=funds,
            scored=scored,
            rankings=rankings,
        )
        print(f"Wrote {OUT / 'curated_backtest_report.md'}")

    all_a20 = [r["alpha_20d"] for r in scored if r.get("alpha_20d") is not None]
    if all_a20:
        import statistics

        med = statistics.median(all_a20)
        hit = sum(1 for a in all_a20 if a > 0) / len(all_a20)
        print(f"DONE: n={len(all_a20)} median a20d={med:.2f}% hit_rate@20d={hit*100:.1f}%")
    else:
        print("DONE: no scorable forward returns (missing tickers or price data)")


if __name__ == "__main__":
    main()
