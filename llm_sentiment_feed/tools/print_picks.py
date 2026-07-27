"""Print LLM panel picks for an issue date."""
from __future__ import annotations

import argparse
import json

import boto3
from boto3.dynamodb.conditions import Key


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--stack", default="llm-sentiment-feed")
    ap.add_argument("--issue-date", required=True)
    ap.add_argument("--test", action="store_true", help="Read test/ prefixed rows")
    args = ap.parse_args()

    table_name = boto3.Session(profile_name=args.profile, region_name=args.region).client(
        "cloudformation"
    ).describe_stacks(StackName=args.stack)["Stacks"][0]["Outputs"]
    table_name = next(o["OutputValue"] for o in table_name if o["OutputKey"] == "SentimentTableName")

    prefix = "test/" if args.test else ""
    pk = f"{prefix}ISSUE#{args.issue_date}"
    table = boto3.resource("dynamodb", region_name=args.region).Table(table_name)
    resp = table.query(KeyConditionExpression=Key("pk").eq(pk))
    items = [i for i in resp.get("Items", []) if str(i.get("sk", "")).startswith("MODEL#")]
    # One row per model_id: prefer latest successful query (prompt_version, queried_at).
    by_model: dict[str, dict] = {}
    for item in items:
        mid = item.get("model_id") or ""
        prev = by_model.get(mid)
        if not prev:
            by_model[mid] = item
            continue
        if item.get("status") == "ok" and prev.get("status") != "ok":
            by_model[mid] = item
            continue
        if (item.get("queried_at") or "") > (prev.get("queried_at") or ""):
            by_model[mid] = item

    print(f"{args.issue_date} — {len(by_model)} models (latest row each)\n")
    for mid in sorted(by_model):
        item = by_model[mid]
        bias = item.get("market_bias") or "?"
        pv = item.get("prompt_version") or "?"
        print(f"## {mid} ({item.get('status')}) bias={bias} prompt={pv}")
        for p in item.get("picks") or []:
            direction = (p.get("direction") or "?").upper()
            print(
                f"  - {p.get('ticker')} | {direction} | {p.get('conviction')} conviction\n"
                f"    {(p.get('rationale') or '')[:120]}"
            )
        if item.get("error"):
            print(f"  error: {item['error'][:200]}")
        if not (item.get("picks") or []) and item.get("status") == "ok":
            print("  (no picks stored — check S3 raw response)")
        print()


if __name__ == "__main__":
    main()
