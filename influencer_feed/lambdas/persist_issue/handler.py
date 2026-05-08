import os
from datetime import datetime, timezone

import boto3

TABLE_NAME = os.environ["TABLE_NAME"]


def handler(event, context):
    issue_date = event["issue_date"]
    user_id = event.get("user_id", "default")
    pk = f"USER#{user_id}"
    sk = f"ISSUE#{issue_date}"

    item = {
        "pk": pk,
        "sk": sk,
        "issue_date": issue_date,
        "status": "COMPOSED",
        "global_summary_smol": event.get("global_summary_smol", ""),
        "global_summary_shift": event.get("global_summary_shift", ""),
        "global_catalysts": event.get("global_catalysts", ""),
        "global_technical": event.get("global_technical", ""),
        "global_ticker_focus": event.get("global_ticker_focus", []),
        "fetched_sources": int(event.get("fetched_sources", 0)),
        "no_new_sources": int(event.get("no_new_sources", 0)),
        "source_count": int(event.get("source_count", 0)),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    gsm = event.get("global_summary_mode")
    if gsm:
        item["global_summary_mode"] = gsm
    gsmodel = event.get("global_summary_model")
    if gsmodel:
        item["global_summary_model"] = gsmodel

    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    table.put_item(Item=item)

    return {
        "status": "COMPOSED",
        "issue_date": issue_date,
        "user_id": user_id,
        "issue_sk": sk,
    }
