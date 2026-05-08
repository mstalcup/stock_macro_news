import json
import os
import time
import uuid
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import boto3

TABLE_NAME = os.environ["TABLE_NAME"]
COMPOSE_ARN = os.environ["COMPOSE_ISSUE_STATE_MACHINE_ARN"]
DEFAULT_USER_ID = os.environ.get("DEFAULT_USER_ID", "default")
SCHEDULE_TZ = os.environ.get("SCHEDULE_LOCAL_TZ", "America/Los_Angeles")
CHECKPOINT_SK = "META#LAST_COMPOSE_WINDOW"


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _default_window_start(now_local: datetime, slot: str) -> datetime:
    if slot == "morning":
        # previous day 2:00pm local
        prev_day = (now_local - timedelta(days=1)).date().isoformat()
        return datetime.fromisoformat(f"{prev_day}T14:00:00").replace(tzinfo=now_local.tzinfo)
    # afternoon slot default: same day 6:00am local
    day = now_local.date().isoformat()
    return datetime.fromisoformat(f"{day}T06:00:00").replace(tzinfo=now_local.tzinfo)


def handler(event, context):
    event = event or {}
    user_id = (event.get("user_id") or DEFAULT_USER_ID).strip() or "default"
    slot = (event.get("slot") or "").strip().lower()
    if slot not in ("morning", "afternoon"):
        # allow manual runs without slot; fallback to nearest local run context
        slot = "morning" if datetime.now(ZoneInfo(SCHEDULE_TZ)).hour < 12 else "afternoon"

    now_local = datetime.now(ZoneInfo(SCHEDULE_TZ))
    window_end_utc = now_local.astimezone(timezone.utc)

    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    pk = f"USER#{user_id}"
    meta = table.get_item(Key={"pk": pk, "sk": CHECKPOINT_SK}).get("Item") or {}
    last_end = meta.get("last_window_end_utc")
    if last_end:
        try:
            window_start_utc = datetime.fromisoformat(last_end.replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            window_start_utc = _default_window_start(now_local, slot).astimezone(timezone.utc)
    else:
        window_start_utc = _default_window_start(now_local, slot).astimezone(timezone.utc)

    issue_date = now_local.date().isoformat()
    compose_input = {
        "issue_date": issue_date,
        "user_id": user_id,
        "window_start_utc": _iso(window_start_utc),
        "window_end_utc": _iso(window_end_utc),
        "compose_slot": slot,
    }

    sfn = boto3.client("stepfunctions")
    ex = sfn.start_execution(
        stateMachineArn=COMPOSE_ARN,
        name=f"compose-window-{issue_date}-{slot}-{uuid.uuid4().hex[:8]}",
        input=json.dumps(compose_input),
    )
    ex_arn = ex["executionArn"]

    # Wait for ComposeIssue; update checkpoint only on success.
    status = "RUNNING"
    for _ in range(240):  # up to ~20 minutes
        d = sfn.describe_execution(executionArn=ex_arn)
        status = d["status"]
        if status != "RUNNING":
            break
        time.sleep(5)

    checkpoint_updated = False
    if status == "SUCCEEDED":
        table.put_item(
            Item={
                "pk": pk,
                "sk": CHECKPOINT_SK,
                "last_window_end_utc": _iso(window_end_utc),
                "last_compose_slot": slot,
                "last_compose_issue_date": issue_date,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        checkpoint_updated = True

    return {
        "status": status,
        "user_id": user_id,
        "slot": slot,
        "issue_date": issue_date,
        "window_start_utc": _iso(window_start_utc),
        "window_end_utc": _iso(window_end_utc),
        "compose_execution_arn": ex_arn,
        "checkpoint_updated": checkpoint_updated,
    }
