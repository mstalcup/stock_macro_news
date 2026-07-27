"""Start LLM sentiment panel Step Functions after macro digest (pre_open only)."""
from __future__ import annotations

import json
import os
import uuid

import boto3

SFN_ARN = (
    os.environ.get("RUN_SENTIMENT_STATE_MACHINE_ARN")
    or os.environ.get("QUERY_PANEL_STATE_MACHINE_ARN")
    or ""
).strip()
SKIP = (os.environ.get("SKIP_LLM_PANEL") or "").lower() in ("1", "true", "yes")
PANEL_SLOTS = frozenset(
    s.strip()
    for s in (os.environ.get("LLM_PANEL_SLOTS") or "pre_open").split(",")
    if s.strip()
)


def handler(event, context):
    event = event or {}
    issue_date = (event.get("issue_date") or "").strip()
    macro_slot = (event.get("macro_slot") or event.get("slot") or "pre_open").strip()

    if SKIP:
        return {"status": "skipped_env", "issue_date": issue_date, "macro_slot": macro_slot}
    if macro_slot not in PANEL_SLOTS:
        return {
            "status": "skipped_slot",
            "issue_date": issue_date,
            "macro_slot": macro_slot,
            "allowed_slots": sorted(PANEL_SLOTS),
        }
    if not SFN_ARN:
        return {"status": "skipped_no_arn", "issue_date": issue_date}

    payload = {"issue_date": issue_date, "macro_slot": macro_slot}
    name = f"macro-{macro_slot}-{issue_date}-{uuid.uuid4().hex[:8]}"[:80]
    out = boto3.client("stepfunctions").start_execution(
        stateMachineArn=SFN_ARN,
        name=name,
        input=json.dumps(payload),
    )
    return {
        "status": "started",
        "issue_date": issue_date,
        "macro_slot": macro_slot,
        "executionArn": out["executionArn"],
    }
