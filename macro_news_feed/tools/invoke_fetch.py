"""Start FetchNews Step Functions execution for one slot."""
from __future__ import annotations

import argparse
import json
import time
import uuid

import boto3


def _wait_sfn(sf, execution_arn: str, poll_s: float = 3.0) -> dict:
    while True:
        desc = sf.describe_execution(executionArn=execution_arn)
        st = desc["status"]
        if st in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
            if st != "SUCCEEDED":
                raise SystemExit(
                    f"Execution {st}: {desc.get('error', '')} {desc.get('cause', '')[:800]}"
                )
            return json.loads(desc.get("output") or "{}")
        time.sleep(poll_s)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--stack", default="macro-news-feed")
    ap.add_argument("--slot", choices=["pre_open", "pre_close"], required=True)
    ap.add_argument("--issue-date", default="")
    ap.add_argument("--no-wait", action="store_true")
    args = ap.parse_args()

    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    cf = sess.client("cloudformation")
    sf = sess.client("stepfunctions")

    arn = next(
        o["OutputValue"]
        for o in cf.describe_stacks(StackName=args.stack)["Stacks"][0]["Outputs"]
        if o["OutputKey"] == "FetchNewsStateMachineArn"
    )

    payload = {"slot": args.slot}
    if args.issue_date:
        payload["issue_date"] = args.issue_date

    name = f"manual-{args.slot}-{payload.get('issue_date', 'today')}-{uuid.uuid4().hex[:8]}"[:80]
    out = sf.start_execution(stateMachineArn=arn, name=name, input=json.dumps(payload))
    ex = out["executionArn"]
    print(f"Started: {ex}")
    if args.no_wait:
        return
    result = _wait_sfn(sf, ex)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
