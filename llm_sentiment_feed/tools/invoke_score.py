"""Invoke score-recommendations Lambda (live rules; optional test quote)."""
from __future__ import annotations

import argparse
import json

import boto3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--issue-date", default="")
    ap.add_argument("--allow-test-quote", action="store_true")
    args = ap.parse_args()

    payload: dict = {}
    if args.issue_date:
        payload["issue_date"] = args.issue_date
    if args.allow_test_quote:
        payload["allow_test_quote"] = True

    lam = boto3.Session(profile_name=args.profile, region_name=args.region).client("lambda")
    resp = lam.invoke(
        FunctionName="llm-sentiment-feed-score-recommendations",
        Payload=json.dumps(payload).encode(),
    )
    print(json.dumps(json.loads(resp["Payload"].read()), indent=2))


if __name__ == "__main__":
    main()
