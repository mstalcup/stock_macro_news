import os

import boto3

TABLE_NAME = os.environ["TABLE_NAME"]


def handler(event, context):
    issue_date = event["issue_date"]
    user_id = event.get("user_id", "default")
    pk = f"USER#{user_id}"

    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    resp = table.query(
        KeyConditionExpression="pk = :pk AND begins_with(sk, :pref)",
        ExpressionAttributeValues={":pk": pk, ":pref": "SOURCE#"},
    )

    sources = []
    for item in resp.get("Items", []):
        if item.get("enabled") is False:
            continue
        sid = item.get("source_id") or item["sk"].replace("SOURCE#", "", 1)
        sources.append(
            {
                "source_id": sid,
                "platform": item.get("platform", "youtube"),
                "channel_id": item.get("channel_id"),
                "channel_handle": item.get("channel_handle"),
                "display_name": item.get("display_name", sid),
            }
        )

    return {"issue_date": issue_date, "user_id": user_id, "sources": sources}
