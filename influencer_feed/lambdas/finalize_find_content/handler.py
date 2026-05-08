import json
import os
import uuid

import boto3

COMPOSE_ARN = os.environ["COMPOSE_ISSUE_STATE_MACHINE_ARN"]


def handler(event, context):
    issue_date = event["issue_date"]
    user_id = event.get("user_id", "default")
    ingest_results = event.get("ingest_results", [])
    start_compose = event.get("start_compose", True)

    if not start_compose:
        return {
            "issue_date": issue_date,
            "user_id": user_id,
            "compose_execution_arn": "",
            "compose_started": False,
        }

    sfn = boto3.client("stepfunctions")
    name = f"compose-{issue_date}-{user_id}-{uuid.uuid4().hex[:8]}"
    payload = {"issue_date": issue_date, "user_id": user_id, "ingest_count": len(ingest_results)}
    resp = sfn.start_execution(
        stateMachineArn=COMPOSE_ARN,
        name=name,
        input=json.dumps(payload),
    )

    return {
        "issue_date": issue_date,
        "user_id": user_id,
        "compose_execution_arn": resp["executionArn"],
        "compose_started": True,
    }
