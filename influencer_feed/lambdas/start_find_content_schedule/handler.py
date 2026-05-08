import json
import os
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3

FIND_CONTENT_ARN = os.environ["FIND_CONTENT_STATE_MACHINE_ARN"]
DEFAULT_USER_ID = os.environ.get("DEFAULT_USER_ID", "default")
DEFAULT_START_COMPOSE = (os.environ.get("DEFAULT_START_COMPOSE", "false").strip().lower() == "true")
LOCAL_TZ = os.environ.get("SCHEDULE_LOCAL_TZ", "America/Los_Angeles")


def _compute_issue_date(event: dict) -> str:
    explicit = (event.get("issue_date") or "").strip()
    if explicit:
        return explicit
    return datetime.now(ZoneInfo(LOCAL_TZ)).date().isoformat()


def handler(event, context):
    event = event or {}
    issue_date = _compute_issue_date(event)
    user_id = (event.get("user_id") or DEFAULT_USER_ID).strip() or "default"
    start_compose = bool(event.get("start_compose", DEFAULT_START_COMPOSE))

    payload = {
        "issue_date": issue_date,
        "user_id": user_id,
        "start_compose": start_compose,
    }

    sfn = boto3.client("stepfunctions")
    execution = sfn.start_execution(
        stateMachineArn=FIND_CONTENT_ARN,
        input=json.dumps(payload),
    )

    return {
        "started": True,
        "issue_date": issue_date,
        "user_id": user_id,
        "start_compose": start_compose,
        "find_content_execution_arn": execution["executionArn"],
    }
