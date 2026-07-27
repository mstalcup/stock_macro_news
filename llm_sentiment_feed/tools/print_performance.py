"""Summarize live pick scoring from DynamoDB (production + optional test)."""
from __future__ import annotations

import argparse
from collections import defaultdict

import boto3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--stack", default="llm-sentiment-feed")
    ap.add_argument("--test", action="store_true")
    args = ap.parse_args()

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    outs = {
        o["OutputKey"]: o["OutputValue"]
        for o in session.client("cloudformation")
        .describe_stacks(StackName=args.stack)["Stacks"][0]["Outputs"]
    }
    table = session.resource("dynamodb").Table(outs["SentimentTableName"])

    picks: list[dict] = []
    scan_kw: dict = {
        "FilterExpression": "begins_with(sk, :p)",
        "ExpressionAttributeValues": {":p": "PICK#"},
    }
    while True:
        r = table.scan(**scan_kw)
        picks.extend(r.get("Items", []))
        if "LastEvaluatedKey" not in r:
            break
        scan_kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    if args.test:
        rows = [p for p in picks if str(p.get("sk", "")).startswith("test/") or p.get("test_run")]
        label = "TEST"
    else:
        rows = [p for p in picks if not str(p.get("sk", "")).startswith("test/") and not p.get("test_run")]
        label = "PRODUCTION"

    print(f"=== {label}: {len(rows)} pick rows ===")
    by_status: dict[str, int] = defaultdict(int)
    returns_7: list[float] = []
    returns_30: list[float] = []
    issues: set[str] = set()
    models: set[str] = set()
    for p in rows:
        issues.add(str(p.get("issue_date") or ""))
        models.add(str(p.get("model_id") or ""))
        st = str(p.get("entry_status") or "pending_close")
        by_status[st] += 1
        if p.get("return_7d") is not None:
            returns_7.append(float(p["return_7d"]))
        if p.get("return_30d") is not None:
            returns_30.append(float(p["return_30d"]))

    print("issue_dates:", sorted(issues))
    print("models:", sorted(models))
    print("entry_status:", dict(by_status))
    print(f"with entry_price: {sum(1 for p in rows if p.get('entry_price') is not None)}/{len(rows)}")
    if returns_7:
        wins = sum(1 for x in returns_7 if x > 0)
        print(
            f"return_7d scored: {len(returns_7)} "
            f"avg={sum(returns_7)/len(returns_7):.2f}% wins={wins}/{len(returns_7)}"
        )
    else:
        print("return_7d scored: 0")
    if returns_30:
        wins = sum(1 for x in returns_30 if x > 0)
        print(
            f"return_30d scored: {len(returns_30)} "
            f"avg={sum(returns_30)/len(returns_30):.2f}% wins={wins}/{len(returns_30)}"
        )
    else:
        print("return_30d scored: 0")

    scored = [
        p
        for p in rows
        if p.get("entry_price") or p.get("return_7d") is not None or p.get("return_30d") is not None
    ]
    for p in sorted(scored, key=lambda x: (x.get("issue_date", ""), x.get("model_id", ""), x.get("ticker", ""))):
        print(
            f"  {p.get('issue_date')} {p.get('model_id'):12} {p.get('ticker'):6} "
            f"{(p.get('direction') or '?'):5} entry={p.get('entry_price')} "
            f"st={p.get('entry_status')} r7={p.get('return_7d')} r30={p.get('return_30d')}"
        )

    # Panel runs
    issues_rows: list[dict] = []
    scan_kw2: dict = {
        "FilterExpression": "begins_with(pk, :i) AND begins_with(sk, :m)",
        "ExpressionAttributeValues": {":i": "ISSUE#", ":m": "MODEL#"},
    }
    while True:
        r = table.scan(**scan_kw2)
        issues_rows.extend(r.get("Items", []))
        if "LastEvaluatedKey" not in r:
            break
        scan_kw2["ExclusiveStartKey"] = r["LastEvaluatedKey"]
    if not args.test:
        issues_rows = [i for i in issues_rows if not str(i.get("pk", "")).startswith("test/")]
    else:
        issues_rows = [i for i in issues_rows if str(i.get("pk", "")).startswith("test/")]

    print("\n=== Panel runs ===")
    for i in sorted(issues_rows, key=lambda x: x.get("issue_date", "")):
        qa = str(i.get("queried_at") or "")[:19]
        print(
            f"  {i.get('issue_date')} {i.get('model_id'):12} "
            f"status={i.get('status')} picks={i.get('pick_count')} queried={qa}"
        )


if __name__ == "__main__":
    main()
