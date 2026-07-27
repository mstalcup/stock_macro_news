"""Start QueryPanel Step Functions execution."""
from __future__ import annotations

import argparse
import json
import time
import uuid

import boto3


def _wait(sf, arn: str) -> dict:
    while True:
        d = sf.describe_execution(executionArn=arn)
        if d["status"] in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
            if d["status"] != "SUCCEEDED":
                raise SystemExit(f"{d['status']}: {d.get('cause', '')[:600]}")
            return json.loads(d.get("output") or "{}")
        time.sleep(4)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--stack", default="llm-sentiment-feed")
    ap.add_argument("--issue-date", default="")
    ap.add_argument("--force-refresh", action="store_true")
    ap.add_argument("--no-wait", action="store_true")
    args = ap.parse_args()

    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    arn = next(
        o["OutputValue"]
        for o in sess.client("cloudformation").describe_stacks(StackName=args.stack)[
            "Stacks"
        ][0]["Outputs"]
        if o["OutputKey"] in ("RunSentimentStateMachineArn", "QueryPanelStateMachineArn")
    )
    payload = {"macro_slot": "pre_open"}
    if args.issue_date:
        payload["issue_date"] = args.issue_date
    if args.force_refresh:
        payload["force_refresh"] = True

    ex = sess.client("stepfunctions").start_execution(
        stateMachineArn=arn,
        name=f"panel-{payload.get('issue_date', 'today')}-{uuid.uuid4().hex[:8]}"[:80],
        input=json.dumps(payload),
    )["executionArn"]
    print(f"Started {ex}")
    if args.no_wait:
        return
    print(json.dumps(_wait(sess.client("stepfunctions"), ex), indent=2))


if __name__ == "__main__":
    main()
