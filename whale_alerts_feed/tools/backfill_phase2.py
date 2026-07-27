"""Backfill Phase 2 signal layers into output/cache/."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parent
sys.path.insert(0, str(ROOT))


def _load_env_keys() -> None:
    """Load FINNHUB_API_KEY from repo .env files if not already set."""
    import os

    if os.environ.get("FINNHUB_API_KEY"):
        return
    for path in (REPO / "llm_sentiment_feed" / ".env", REPO / ".env", ROOT / ".env"):
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("FINNHUB_API_KEY=") and "=" in line:
                os.environ["FINNHUB_API_KEY"] = line.split("=", 1)[1].strip().strip('"')
                return

from whalelib.eight_k import eight_k_signals_from_schedule, efts_8k_stake_signals, roster_8k_signals
from whalelib.news_signals import news_stake_signals_for_events
from whalelib.volume_signals import volume_spike_signals_for_events

CACHE = ROOT / "output" / "cache"
ROSTER = ROOT / "seed" / "fund_roster_curated.json"
CURATED_SCORED = ROOT / "output" / "curated_scored_signals.json"


def _load_edgar_anchor_events() -> list[dict]:
    """13D/G + 8-K events with tickers — anchors for volume/news lookback."""
    events: list[dict] = []
    if CURATED_SCORED.is_file():
        for row in json.loads(CURATED_SCORED.read_text(encoding="utf-8")):
            st = row.get("signal_type") or ""
            if st.startswith("13"):
                events.append(
                    {
                        "file_date": row.get("signal_date"),
                        "issuer_ticker": row.get("ticker"),
                        "filer_cik": row.get("filer_cik"),
                        "filer_name": row.get("filer_name"),
                        "signal_type": st,
                    }
                )
    eight_k_path = CACHE / "8k_hits.json"
    if eight_k_path.is_file():
        for row in json.loads(eight_k_path.read_text(encoding="utf-8")):
            events.append(row)
    return events


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=2)
    ap.add_argument("--skip-volume", action="store_true")
    ap.add_argument("--skip-news", action="store_true")
    ap.add_argument("--8k-limit", type=int, default=200, dest="eight_k_limit", help="Max EFTS 8-K to parse")
    ap.add_argument("--volume-limit", type=int, default=120, help="Max EDGAR anchor events for volume scan")
    ap.add_argument("--news-limit", type=int, default=80, help="Max EDGAR anchor events for news scan")
    args = ap.parse_args()

    _load_env_keys()

    if not ROSTER.is_file():
        print("Missing seed/fund_roster_curated.json")
        raise SystemExit(1)

    funds = json.loads(ROSTER.read_text(encoding="utf-8"))
    CACHE.mkdir(parents=True, exist_ok=True)

    print(f"Phase 2 backfill — {len(funds)} funds, ~{args.years}y")

    # Layer: 8-K stake
    schedule_rows = []
    if CURATED_SCORED.is_file():
        schedule_rows = [
            r for r in json.loads(CURATED_SCORED.read_text(encoding="utf-8"))
            if (r.get("signal_type") or "").startswith("13")
        ]
    print(f"  Issuer 8-K lookback before {len(schedule_rows)} schedule filings...")
    sched_8k = eight_k_signals_from_schedule(schedule_rows, funds, lookback_days=45)
    print(f"  EFTS 8-K discovery (limit {args.eight_k_limit})...")
    efts_8k = efts_8k_stake_signals(funds, years=int(args.years), parse_limit=args.eight_k_limit)
    roster_8k = roster_8k_signals(funds, years=int(args.years))
    merged = {s.signal_id: s for s in sched_8k}
    for s in efts_8k + roster_8k:
        merged[s.signal_id] = s
    signals_8k = list(merged.values())
    eight_k_hits = [
        {
            "file_date": s.signal_date,
            "issuer_ticker": s.ticker,
            "filer_cik": s.filer_cik,
            "filer_name": s.filer_name,
            "accession": s.accession,
            "signal_type": "8k_stake",
            "meta": s.meta,
        }
        for s in signals_8k
    ]
    (CACHE / "8k_hits.json").write_text(json.dumps(eight_k_hits, indent=2), encoding="utf-8")
    (CACHE / "8k_signals.json").write_text(
        json.dumps([s.__dict__ for s in signals_8k], indent=2), encoding="utf-8"
    )
    print(
        f"  8-K stake signals: {len(signals_8k)} "
        f"(before-13D: {len(sched_8k)}, EFTS: {len(efts_8k)}, roster CIK: {len(roster_8k)})"
    )

    anchors = _load_edgar_anchor_events()
    # Prefer 13d_new / 8k for anchors (stronger events)
    anchors.sort(
        key=lambda x: (
            0 if x.get("signal_type") in ("13d_new", "8k_stake") else 1,
            x.get("file_date") or "",
        )
    )

    if not args.skip_volume:
        vol_events = anchors[: args.volume_limit]
        print(f"  Volume scan on {len(vol_events)} anchor events...")
        vol_sigs = volume_spike_signals_for_events(vol_events)
        (CACHE / "volume_spike_signals.json").write_text(
            json.dumps([s.__dict__ for s in vol_sigs], indent=2), encoding="utf-8"
        )
        print(f"  Volume spike signals: {len(vol_sigs)}")
    else:
        print("  Skipping volume layer")

    if not args.skip_news:
        news_events = anchors[: args.news_limit]
        print(f"  News scan on {len(news_events)} anchor events (needs FINNHUB_API_KEY)...")
        news_sigs = news_stake_signals_for_events(news_events)
        (CACHE / "news_stake_signals.json").write_text(
            json.dumps([s.__dict__ for s in news_sigs], indent=2), encoding="utf-8"
        )
        print(f"  News stake signals: {len(news_sigs)}")
    else:
        print("  Skipping news layer")

    print(f"Wrote {CACHE}")


if __name__ == "__main__":
    main()
