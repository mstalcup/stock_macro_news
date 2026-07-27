"""Invoke compose-news-slot Lambda for a date/slot."""
from __future__ import annotations

import argparse
import json

import boto3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--issue-date", required=True)
    ap.add_argument("--slot", choices=["pre_open", "pre_close"], required=True)
    args = ap.parse_args()

    payload = {
        "issue_date": args.issue_date,
        "slot": args.slot,
        "deduped_s3_key": f"v1/date={args.issue_date}/slot={args.slot}/deduped.json",
        "fetch_status": "ok",
    }
    lam = boto3.Session(profile_name=args.profile, region_name=args.region).client("lambda")
    resp = lam.invoke(
        FunctionName="macro-news-feed-compose-news-slot",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    body = json.loads(resp["Payload"].read())
    if resp.get("FunctionError"):
        raise SystemExit(json.dumps(body, indent=2))
    print(json.dumps(body, indent=2))


if __name__ == "__main__":
    main()
