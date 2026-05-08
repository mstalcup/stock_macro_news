import os

import boto3

TABLE_NAME = os.environ["TABLE_NAME"]


def handler(event, context):
    issue_date = event["issue_date"]
    user_id = event.get("user_id", "default")
    pk = f"USER#{user_id}"
    prefix = f"FETCH#{issue_date}#"

    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    resp = table.query(
        KeyConditionExpression="pk = :pk AND begins_with(sk, :pref)",
        ExpressionAttributeValues={":pk": pk, ":pref": prefix},
    )
    rows = resp.get("Items", [])

    fetched_rows = [row for row in rows if row.get("status") == "FETCHED"]
    no_new_rows = [row for row in rows if row.get("status") == "NO_NEW"]
    # Stub issue document — replace with LLM + S3 artifact writes later.
    global_narrative = (
        f"[stub] Summarized {len(fetched_rows)} updated source(s), "
        f"{len(no_new_rows)} no-update source(s), for {issue_date} ({user_id}). "
        "Wire Anthropic/OpenAI here and merge per your smol-style prompt."
    )

    return {
        "status": "COMPOSED_STUB",
        "issue_date": issue_date,
        "user_id": user_id,
        "global_narrative": global_narrative,
        "fetched_sources": len(fetched_rows),
        "no_new_sources": len(no_new_rows),
    }
