"""List recent ComposeIssue executions for default user with Discord status."""
from __future__ import annotations

import argparse
import json

import boto3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--stack", default="influencer-feed")
    ap.add_argument("--limit", type=int, default=20)
    args = ap.parse_args()
    sess = boto3.Session(profile_name=args.profile, region_name="us-east-1")
    cf = sess.client("cloudformation")
    sfn = sess.client("stepfunctions")
    arn = next(
        o["OutputValue"]
        for o in cf.describe_stacks(StackName=args.stack)["Stacks"][0]["Outputs"]
        if o["OutputKey"] == "ComposeIssueStateMachineArn"
    )
    resp = sfn.list_executions(stateMachineArn=arn, maxResults=args.limit)
    for ex in resp.get("executions", []):
        name = ex.get("name", "")
        if "crypto" in name:
            continue
        d = sfn.describe_execution(executionArn=ex["executionArn"])
        out = json.loads(d.get("output") or "{}")
        started = str(ex["startDate"])[:16]
        print(
            started,
            d["status"],
            name[:50],
            "|",
            out.get("discord_publish_status"),
            "|",
            out.get("issue_date"),
        )


if __name__ == "__main__":
    main()
