"""
Run FindContent (no compose) then ComposeIssue for each issue_date in the last N
calendar days (America/Los_Angeles), for one or more user_id values.

FindContent uses start_compose=false so compose runs are explicit (Discord can be
skipped during compose via SKIP_DISCORD_PUBLISH on publish-discord).

Examples:
  py tools/backfill_recent_days.py --days 7 --users default,crypto
  py tools/backfill_recent_days.py --days 3 --fetch-only
  py tools/backfill_recent_days.py --days 7 --compose-only --skip-discord
"""

from __future__ import annotations

import argparse
import json
import time
import uuid
from datetime import timedelta

import boto3


def _stack_output(cf, stack: str, key: str) -> str:
    stacks = cf.describe_stacks(StackName=stack)["Stacks"]
    outs = {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}
    val = outs.get(key)
    if not val:
        raise SystemExit(f"Stack {stack} has no output {key}")
    return val


def _lambda_physical_name(cf, stack: str, logical_id: str) -> str:
    res = cf.describe_stack_resources(StackName=stack)["StackResources"]
    for r in res:
        if r.get("LogicalResourceId") == logical_id:
            return r["PhysicalResourceId"]
    raise SystemExit(f"No resource {logical_id} in stack {stack}")


def _la_dates_last_n_days(n: int) -> list[str]:
    """Oldest first: today (LA) and the previous n-1 calendar days."""
    from datetime import datetime as dt
    from zoneinfo import ZoneInfo

    LA = ZoneInfo("America/Los_Angeles")
    today = dt.now(LA).date()
    out: list[str] = []
    for i in range(n - 1, -1, -1):
        out.append((today - timedelta(days=i)).isoformat())
    return out


def _wait_sfn(sf, execution_arn: str, poll_s: float = 4.0) -> str:
    while True:
        desc = sf.describe_execution(executionArn=execution_arn)
        st = desc["status"]
        if st in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
            if st != "SUCCEEDED":
                err = desc.get("error", "")
                cause = (desc.get("cause") or "")[:1500]
                raise SystemExit(f"Step Functions {st}: {err} {cause}")
            return st
        time.sleep(poll_s)


def _set_skip_discord(lam, fn_name: str, skip: bool) -> None:
    cfg = lam.get_function_configuration(FunctionName=fn_name)
    env = dict(cfg.get("Environment", {}).get("Variables", {}))
    env["SKIP_DISCORD_PUBLISH"] = "true" if skip else "false"
    lam.update_function_configuration(FunctionName=fn_name, Environment={"Variables": env})
    # Wait until the update is applied.
    for _ in range(30):
        cfg2 = lam.get_function_configuration(FunctionName=fn_name)
        st = cfg2.get("LastUpdateStatus", "")
        if st in ("Successful", ""):
            break
        time.sleep(2)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="mastalcup", help="AWS profile name")
    ap.add_argument("--region", default="us-east-1", help="AWS region")
    ap.add_argument("--stack", default="influencer-feed", help="CloudFormation stack name")
    ap.add_argument("--days", type=int, default=7, help="Number of calendar days (LA) to cover")
    ap.add_argument(
        "--users",
        default="default,crypto",
        help="Comma-separated user_id values (e.g. default,crypto)",
    )
    ap.add_argument("--fetch-only", action="store_true", help="Only run FindContent")
    ap.add_argument("--compose-only", action="store_true", help="Only run ComposeIssue")
    ap.add_argument(
        "--skip-discord",
        action="store_true",
        help="Set SKIP_DISCORD_PUBLISH on publish-discord during compose phase",
    )
    ap.add_argument("--dry-run", action="store_true", help="Print plan only")
    args = ap.parse_args()

    if args.fetch_only and args.compose_only:
        raise SystemExit("Use at most one of --fetch-only / --compose-only")

    users = [u.strip() for u in args.users.split(",") if u.strip()]
    if not users:
        raise SystemExit("No users in --users")

    dates = _la_dates_last_n_days(args.days)
    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    cf = sess.client("cloudformation")
    sf = sess.client("stepfunctions")
    lam = sess.client("lambda")

    find_arn = _stack_output(cf, args.stack, "FindContentStateMachineArn")
    compose_arn = _stack_output(cf, args.stack, "ComposeIssueStateMachineArn")
    discord_name = _lambda_physical_name(cf, args.stack, "PublishDiscordFunction")

    print(
        f"Backfill {args.days} day(s), LA calendar dates: {dates[0]} .. {dates[-1]} "
        f"({len(dates)} dates), users={users}"
    )
    if args.dry_run:
        print("Dry run: no executions.")
        return

    def run_find(issue_date: str, user_id: str) -> None:
        name = f"bf-find-{user_id}-{issue_date}-{uuid.uuid4().hex[:8]}"[:80]
        out = sf.start_execution(
            stateMachineArn=find_arn,
            name=name,
            input=json.dumps(
                {"issue_date": issue_date, "user_id": user_id, "start_compose": False}
            ),
        )
        ex = out["executionArn"]
        print(f"FindContent {issue_date} {user_id} -> {ex}")
        _wait_sfn(sf, ex)
        print(f"FindContent {issue_date} {user_id} OK")

    def run_compose(issue_date: str, user_id: str) -> None:
        name = f"bf-cmp-{user_id}-{issue_date}-{uuid.uuid4().hex[:8]}"[:80]
        out = sf.start_execution(
            stateMachineArn=compose_arn,
            name=name,
            input=json.dumps({"issue_date": issue_date, "user_id": user_id}),
        )
        ex = out["executionArn"]
        print(f"ComposeIssue {issue_date} {user_id} -> {ex}")
        _wait_sfn(sf, ex)
        print(f"ComposeIssue {issue_date} {user_id} OK")

    try:
        if not args.compose_only:
            for d in dates:
                for uid in users:
                    run_find(d, uid)

        if not args.fetch_only:
            if args.skip_discord:
                print("Setting SKIP_DISCORD_PUBLISH=true on publish-discord...")
                _set_skip_discord(lam, discord_name, True)
            for d in dates:
                for uid in users:
                    run_compose(d, uid)
    finally:
        if args.skip_discord and not args.fetch_only:
            print("Restoring SKIP_DISCORD_PUBLISH=false on publish-discord...")
            _set_skip_discord(lam, discord_name, False)

    print("Done.")


if __name__ == "__main__":
    main()
