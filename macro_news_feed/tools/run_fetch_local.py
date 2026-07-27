"""
Fetch macro news locally (prints summary; optional S3 upload).

  cd macro_news_feed
  py tools/run_fetch_local.py --slot pre_open
  py tools/run_fetch_local.py --slot pre_close --write-s3 --bucket my-bucket --profile mastalcup

Loads ALPHA_VANTAGE_API_KEY, NEWS_API_KEY, FINNHUB_API_KEY from repo .env.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

FEED = Path(__file__).resolve().parents[1]
ROOT = FEED.parent
LAMBDA = FEED / "lambdas" / "fetch_news_slot"
sys.path.insert(0, str(LAMBDA))

for env_path in (ROOT / ".env", FEED / ".env"):
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _fetch_only(slot: str, issue_date: str | None) -> dict:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from newslib.config import ENABLED_PROVIDERS, SLOT_LABELS
    from newslib.dedupe import dedupe_articles
    from newslib.fetchers import fetch_alpha_vantage, fetch_finnhub, fetch_newsapi
    from newslib.secrets import load_api_keys
    from newslib.window import issue_window

    keys = load_api_keys()
    tz = os.environ.get("SCHEDULE_LOCAL_TZ", "America/Los_Angeles")
    idate = issue_date or datetime.now(ZoneInfo(tz)).date().isoformat()
    ws, we = issue_window(idate, tz)
    by_provider: dict[str, list] = {}
    errors: dict[str, str | None] = {}

    if "alpha_vantage" in ENABLED_PROVIDERS:
        av, e1 = fetch_alpha_vantage(keys["alpha_vantage"], window_start=ws, window_end=we)
        by_provider["alpha_vantage"] = av
        errors["alpha_vantage"] = e1
    else:
        by_provider["alpha_vantage"] = []
        errors["alpha_vantage"] = "disabled"

    if "newsapi" in ENABLED_PROVIDERS:
        na, e2 = fetch_newsapi(keys["newsapi"], window_start=ws, window_end=we)
        by_provider["newsapi"] = na
        errors["newsapi"] = e2
    else:
        by_provider["newsapi"] = []
        errors["newsapi"] = "disabled"

    if "finnhub" in ENABLED_PROVIDERS:
        fh, e3 = fetch_finnhub(keys["finnhub"], window_start=ws, window_end=we)
        by_provider["finnhub"] = fh
        errors["finnhub"] = e3
    else:
        by_provider["finnhub"] = []
        errors["finnhub"] = "disabled"

    deduped, stats = dedupe_articles(by_provider)
    return {
        "issue_date": issue_date,
        "slot": slot,
        "slot_label": SLOT_LABELS.get(slot, slot),
        "counts": {k: len(v) for k, v in by_provider.items()},
        "deduped": len(deduped),
        "dedupe_stats": stats,
        "errors": {k: v for k, v in errors.items() if v},
        "top_headlines": [{"title": a["title"], "providers": a["providers"]} for a in deduped[:12]],
        "_by_provider": by_provider,
        "_deduped": deduped,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slot", choices=["pre_open", "pre_close"], default="pre_open")
    ap.add_argument("--issue-date", default="")
    ap.add_argument("--write-s3", action="store_true")
    ap.add_argument("--bucket", default="", help="S3 bucket (or NEWS_ARTIFACTS_BUCKET env)")
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "mastalcup"))
    args = ap.parse_args()

    if args.write_s3:
        bucket = args.bucket or os.environ.get("NEWS_ARTIFACTS_BUCKET", "")
        if not bucket:
            raise SystemExit("--bucket or NEWS_ARTIFACTS_BUCKET required for --write-s3")
        os.environ["NEWS_ARTIFACTS_BUCKET"] = bucket
        os.environ.setdefault("SCHEDULE_LOCAL_TZ", "America/Los_Angeles")
        if args.profile:
            os.environ["AWS_PROFILE"] = args.profile
        from handler import handler

        event = {"slot": args.slot}
        if args.issue_date:
            event["issue_date"] = args.issue_date
        print(json.dumps(handler(event, None), indent=2))
        return

    out = _fetch_only(args.slot, args.issue_date.strip() if args.issue_date else None)
    out.pop("_by_provider", None)
    out.pop("_deduped", None)
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
