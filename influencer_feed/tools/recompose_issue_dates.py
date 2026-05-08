"""
Re-run ComposeIssue for each issue_date that has ISSUE_SOURCE rows.

Use this after changing summarization logic or to replace old rows that stored
transcript excerpts instead of LLM summaries.

Examples:
  py tools/recompose_issue_dates.py --dry-run
  py tools/recompose_issue_dates.py
"""

from __future__ import annotations

import argparse
import json
import time
import uuid

import boto3


def _compose_arn(cf, stack: str) -> str:
    stacks = cf.describe_stacks(StackName=stack)["Stacks"]
    outs = {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}
    arn = outs.get("ComposeIssueStateMachineArn")
    if not arn:
        raise SystemExit(f"Stack {stack} has no ComposeIssueStateMachineArn output")
    return arn


def _table_name(cf, stack: str) -> str:
    stacks = cf.describe_stacks(StackName=stack)["Stacks"]
    outs = {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}
    name = outs.get("InfluencerFeedTable")
    if not name:
        raise SystemExit(f"Stack {stack} has no InfluencerFeedTable output")
    return name


def _issue_dates_from_issue_sources(table, pk: str) -> list[str]:
    prefix = "ISSUE_SOURCE#"
    dates: set[str] = set()
    kwargs = {
        "KeyConditionExpression": "pk = :pk AND begins_with(sk, :p)",
        "ExpressionAttributeValues": {":pk": pk, ":p": prefix},
    }
    while True:
        resp = table.query(**kwargs)
        for it in resp.get("Items", []):
            sk = it.get("sk", "")
            parts = sk.split("#")
            if len(parts) >= 3 and parts[0] == "ISSUE_SOURCE":
                dates.add(parts[1])
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        kwargs["ExclusiveStartKey"] = lek
    return sorted(dates)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="mastalcup", help="AWS profile name")
    ap.add_argument("--region", default="us-east-1", help="AWS region")
    ap.add_argument("--stack", default="influencer-feed", help="CloudFormation stack name")
    ap.add_argument("--user-id", default="default", help="user_id passed to ComposeIssue")
    ap.add_argument("--dry-run", action="store_true", help="Print dates only, do not start runs")
    ap.add_argument(
        "--dates",
        default="",
        help="Comma-separated issue_date values (YYYY-MM-DD). If set, only these dates are recomposed.",
    )
    args = ap.parse_args()

    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    cf = sess.client("cloudformation")
    sm_arn = _compose_arn(cf, args.stack)
    table_name = _table_name(cf, args.stack)
    table = sess.resource("dynamodb").Table(table_name)
    pk = f"USER#{args.user_id}"

    if args.dates.strip():
        dates = sorted({d.strip() for d in args.dates.split(",") if d.strip()})
    else:
        dates = _issue_dates_from_issue_sources(table, pk)
    if not dates:
        print("No ISSUE_SOURCE rows found; nothing to do.")
        return

    print(f"Found {len(dates)} issue_date(s): {', '.join(dates)}")
    if args.dry_run:
        print("Dry run: no executions started.")
        return

    sf = sess.client("stepfunctions")
    for d in dates:
        name = f"recompose-{d}-{uuid.uuid4().hex[:8]}"
        out = sf.start_execution(
            stateMachineArn=sm_arn,
            name=name,
            input=json.dumps({"issue_date": d, "user_id": args.user_id}),
        )
        ex_arn = out["executionArn"]
        print(d, "started", ex_arn)
        # Avoid Lambda 429s from overlapping ComposeIssue runs (each run maps N sources in parallel).
        while True:
            desc = sf.describe_execution(executionArn=ex_arn)
            st = desc["status"]
            if st in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
                print(d, "finished", st)
                if st != "SUCCEEDED":
                    raise SystemExit(f"ComposeIssue failed for {d}: {st} {desc.get('error','')}")
                break
            time.sleep(3)


if __name__ == "__main__":
    main()
