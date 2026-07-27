import json
import os
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3

SFN_ARN = (
    os.environ.get("RUN_SENTIMENT_STATE_MACHINE_ARN")
    or os.environ.get("QUERY_PANEL_STATE_MACHINE_ARN")
    or ""
).strip()
LOCAL_TZ = os.environ.get("SCHEDULE_LOCAL_TZ", "America/Los_Angeles")


def handler(event, context):
    event = event or {}
    issue_date = (event.get("issue_date") or "").strip()
    if not issue_date:
        issue_date = datetime.now(ZoneInfo(LOCAL_TZ)).date().isoformat()

    payload = {"issue_date": issue_date, "macro_slot": "pre_open"}
    if event.get("force_refresh"):
        payload["force_refresh"] = True
    if event.get("test_run"):
        payload["test_run"] = True

    sf = boto3.client("stepfunctions")
    name = f"panel-{issue_date}-{uuid.uuid4().hex[:8]}"[:80]
    out = sf.start_execution(
        stateMachineArn=SFN_ARN,
        name=name,
        input=json.dumps(payload),
    )
    return {"executionArn": out["executionArn"], "issue_date": issue_date}
