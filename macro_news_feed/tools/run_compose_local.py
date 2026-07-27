"""
Compose macro digest locally from S3 deduped.json (or after local fetch).

  cd macro_news_feed
  py tools/run_compose_local.py --issue-date 2026-05-15 --slot pre_open --bucket <name>
  py tools/run_compose_local.py --latest --slot pre_open --bucket <name>
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

FEED = Path(__file__).resolve().parents[1]
ROOT = FEED.parent
LAMBDA = FEED / "lambdas" / "compose_news_slot"
sys.path.insert(0, str(LAMBDA))

for env_path in (ROOT / ".env", FEED / ".env"):
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "mastalcup"))
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--stack", default="macro-news-feed")
    ap.add_argument("--bucket", default="")
    ap.add_argument("--issue-date", default="")
    ap.add_argument("--slot", choices=["pre_open", "pre_close"], required=True)
    ap.add_argument("--latest", action="store_true")
    ap.add_argument("--deduped-key", default="")
    args = ap.parse_args()

    import boto3

    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    cf = sess.client("cloudformation")
    s3 = sess.client("s3")

    bucket = args.bucket
    if not bucket:
        outs = cf.describe_stacks(StackName=args.stack)["Stacks"][0]["Outputs"]
        bucket = next(o["OutputValue"] for o in outs if o["OutputKey"] == "NewsArtifactsBucket")

    if args.latest:
        ptr = json.loads(
            s3.get_object(Bucket=bucket, Key=f"v1/latest/slot={args.slot}.json")["Body"].read()
        )
        issue_date = ptr["issue_date"]
        deduped_key = ptr["deduped_s3_key"]
    else:
        if not args.issue_date:
            raise SystemExit("--issue-date required unless --latest")
        issue_date = args.issue_date
        deduped_key = (
            args.deduped_key or f"v1/date={issue_date}/slot={args.slot}/deduped.json"
        )

    os.environ["NEWS_ARTIFACTS_BUCKET"] = bucket
    os.environ.setdefault("OPENAI_CHEAP_MODEL", "gpt-4o-mini")
    os.environ.setdefault("OPENAI_SMART_MODEL", "gpt-4o")

    from handler import handler

    result = handler(
        {
            "issue_date": issue_date,
            "slot": args.slot,
            "deduped_s3_key": deduped_key,
            "fetch_status": "local",
        },
        None,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "digest_preview"}, indent=2))
    print("\n--- digest preview ---\n")
    print(result.get("digest_preview", ""))


if __name__ == "__main__":
    main()
