"""Run LLM panel locally (TEST_RUN=true recommended)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

FEED = Path(__file__).resolve().parents[1]
ROOT = FEED.parent
sys.path.insert(0, str(FEED / "lambdas" / "query_llm_panel"))

for p in (ROOT / ".env", FEED / ".env", ROOT / "macro_news_feed" / ".env"):
    if p.is_file():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--issue-date", required=True)
    ap.add_argument("--bucket", default="macro-news-feed-newsartifactsbucket-qfoqryswgxiv")
    ap.add_argument("--sentiment-bucket", default="")
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--force-refresh", action="store_true")
    ap.add_argument("--no-test-prefix", action="store_true")
    args = ap.parse_args()

    if not args.no_test_prefix:
        os.environ["TEST_RUN"] = "true"
    os.environ["MACRO_ARTIFACTS_BUCKET"] = args.bucket
    os.environ.setdefault("AWS_PROFILE", args.profile)

    if args.sentiment_bucket:
        os.environ["SENTIMENT_ARTIFACTS_BUCKET"] = args.sentiment_bucket
    else:
        import boto3

        outs = boto3.Session(profile_name=args.profile).client("cloudformation").describe_stacks(
            StackName="llm-sentiment-feed"
        )["Stacks"][0]["Outputs"]
        os.environ["SENTIMENT_ARTIFACTS_BUCKET"] = next(
            o["OutputValue"] for o in outs if o["OutputKey"] == "SentimentArtifactsBucket"
        )
        os.environ["SENTIMENT_TABLE_NAME"] = next(
            o["OutputValue"] for o in outs if o["OutputKey"] == "SentimentTableName"
        )

    from handler import handler

    event = {"issue_date": args.issue_date, "macro_slot": "pre_open"}
    if args.force_refresh:
        event["force_refresh"] = True
    print(json.dumps(handler(event, None), indent=2))


if __name__ == "__main__":
    main()
