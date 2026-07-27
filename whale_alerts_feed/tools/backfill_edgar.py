"""Backfill EDGAR 13D/G and 13F hits into output/cache/."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whalelib.backtest_config import LOOKBACK_YEARS
from whalelib.edgar import (
    build_name_ticker_map,
    company_tickers_map,
    enrich_schedule_filing,
    search_filings,
)

CACHE = ROOT / "output" / "cache"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=LOOKBACK_YEARS)
    ap.add_argument("--enrich-limit", type=int, default=200, help="Max 13D/G filings to parse for issuer ticker")
    ap.add_argument("--skip-enrich", action="store_true")
    args = ap.parse_args()

    end = date.today()
    start = end - timedelta(days=int(args.years * 365))
    CACHE.mkdir(parents=True, exist_ok=True)

    print(f"Searching EDGAR {start} .. {end}")

    schedule_forms = ["SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"]
    schedule_hits = search_filings(forms=schedule_forms, start_date=start, end_date=end, max_pages=30)
    print(f"  13D/G hits: {len(schedule_hits)}")

    f13_hits = search_filings(forms=["13F-HR"], start_date=start, end_date=end, max_pages=40)
    print(f"  13F-HR hits: {len(f13_hits)}")

    enriched: list[dict] = []
    if not args.skip_enrich and schedule_hits:
        cik_map = company_tickers_map()
        name_map = build_name_ticker_map()
        for i, hit in enumerate(schedule_hits[: args.enrich_limit]):
            if i and i % 25 == 0:
                print(f"  enrich {i}/{min(len(schedule_hits), args.enrich_limit)}")
            enriched.append(enrich_schedule_filing(hit, cik_map, name_map))
        if len(schedule_hits) > args.enrich_limit:
            enriched.extend(schedule_hits[args.enrich_limit :])
    else:
        enriched = schedule_hits

    (CACHE / "schedule_hits.json").write_text(json.dumps(enriched, indent=2), encoding="utf-8")
    (CACHE / "f13_hits.json").write_text(json.dumps(f13_hits, indent=2), encoding="utf-8")
    print(f"Wrote {CACHE / 'schedule_hits.json'}")
    print(f"Wrote {CACHE / 'f13_hits.json'}")


if __name__ == "__main__":
    main()
