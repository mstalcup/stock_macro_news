"""Start FetchNews Step Functions execution (used by EventBridge schedules)."""
import json
import os
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3

FETCH_NEWS_STATE_MACHINE_ARN = os.environ["FETCH_NEWS_STATE_MACHINE_ARN"]
LOCAL_TZ = os.environ.get("SCHEDULE_LOCAL_TZ", "America/Los_Angeles")
VALID_SLOTS = frozenset({"pre_open", "pre_close"})


def _issue_date(event: dict) -> str:
    explicit = (event.get("issue_date") or "").strip()
    if explicit:
        return explicit
    return datetime.now(ZoneInfo(LOCAL_TZ)).date().isoformat()


def handler(event, context):
    event = event or {}
    slot = (event.get("slot") or "").strip()
    if slot not in VALID_SLOTS:
        raise ValueError(f"event.slot must be one of {sorted(VALID_SLOTS)}")

    issue_date = _issue_date(event)
    payload = {"issue_date": issue_date, "slot": slot}

    name = f"{slot}-{issue_date}-{uuid.uuid4().hex[:8]}"[:80]
    sfn = boto3.client("stepfunctions")
    execution = sfn.start_execution(
        stateMachineArn=FETCH_NEWS_STATE_MACHINE_ARN,
        name=name,
        input=json.dumps(payload),
    )

    return {
        "started": True,
        "issue_date": issue_date,
        "slot": slot,
        "fetch_news_execution_arn": execution["executionArn"],
    }
