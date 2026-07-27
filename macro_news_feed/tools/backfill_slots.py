"""Start FetchNews Step Functions for multiple dates/slots."""
from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import date, timedelta

import boto3


def _wait_sfn(sf, execution_arn: str) -> dict:
    while True:
        desc = sf.describe_execution(executionArn=execution_arn)
        st = desc["status"]
        if st in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
            if st != "SUCCEEDED":
                raise SystemExit(f"{execution_arn} {st}: {desc.get('cause', '')[:500]}")
            return json.loads(desc.get("output") or "{}")
        time.sleep(3)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--stack", default="macro-news-feed")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--dates", default="", help="Comma-separated YYYY-MM-DD")
    ap.add_argument("--slots", default="pre_open,pre_close")
    ap.add_argument(
        "--force-refresh",
        action="store_true",
        help="Re-fetch all providers even if by_source/*.json exists in S3",
    )
    args = ap.parse_args()

    if args.dates:
        dates = [d.strip() for d in args.dates.split(",") if d.strip()]
    else:
        today = date.today()
        dates = [(today - timedelta(days=i)).isoformat() for i in range(args.days - 1, -1, -1)]

    slots = [s.strip() for s in args.slots.split(",") if s.strip()]
    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    cf = sess.client("cloudformation")
    sf = sess.client("stepfunctions")
    arn = next(
        o["OutputValue"]
        for o in cf.describe_stacks(StackName=args.stack)["Stacks"][0]["Outputs"]
        if o["OutputKey"] == "FetchNewsStateMachineArn"
    )

    for d in dates:
        for slot in slots:
            payload = {"issue_date": d, "slot": slot}
            if args.force_refresh:
                payload["force_refresh"] = True
            name = f"bf-{slot}-{d}-{uuid.uuid4().hex[:8]}"[:80]
            print(f"Start {d} {slot}...")
            ex = sf.start_execution(stateMachineArn=arn, name=name, input=json.dumps(payload))[
                "executionArn"
            ]
            result = _wait_sfn(sf, ex)
            fetch = result.get("fetch") or result
            compose = result.get("compose") or result
            counts = fetch.get("counts", result.get("counts", {}))
            cached = fetch.get("cached_providers", result.get("cached_providers", []))
            compose_mode = compose.get("compose_mode", result.get("compose_mode", ""))
            line = f"  OK fetch={fetch.get('status')} counts={counts}"
            if cached:
                line += f" cached={cached}"
            if compose_mode:
                line += f" compose={compose_mode}"
            print(line)


if __name__ == "__main__":
    main()
