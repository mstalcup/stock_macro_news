import os
from datetime import datetime, timezone

import boto3

TABLE_NAME = os.environ["TABLE_NAME"]


def _parse_iso_utc(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return dt.astimezone(timezone.utc)
    except ValueError:
        return None


def handler(event, context):
    issue_date = event["issue_date"]
    user_id = event.get("user_id", "default")
    pk = f"USER#{user_id}"
    window_start = _parse_iso_utc(event.get("window_start_utc"))
    window_end = _parse_iso_utc(event.get("window_end_utc"))
    use_window = bool(window_start and window_end)
    prefix = "FETCH#" if use_window else f"FETCH#{issue_date}#"

    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    refs = []
    no_new = 0

    query_kwargs = {
        "KeyConditionExpression": "pk = :pk AND begins_with(sk, :pref)",
        "ExpressionAttributeValues": {":pk": pk, ":pref": prefix},
    }
    rows = []
    while True:
        resp = table.query(**query_kwargs)
        rows.extend(resp.get("Items", []))

        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        query_kwargs["ExclusiveStartKey"] = last_key

    fetched = 0
    if use_window:
        # Keep only latest FETCHED row per source inside [window_start, window_end).
        by_source = {}
        for row in rows:
            status = row.get("status", "UNKNOWN")
            if status == "NO_NEW":
                no_new += 1
            if status != "FETCHED":
                continue
            ts = _parse_iso_utc(row.get("transcript_fetched_at")) or _parse_iso_utc(row.get("updated_at"))
            if not ts or ts < window_start or ts >= window_end:
                continue
            source_id = row.get("source_id") or row["sk"].split("#", 2)[-1]
            prev = by_source.get(source_id)
            if not prev:
                by_source[source_id] = (ts, row)
            else:
                prev_ts, _ = prev
                if ts > prev_ts:
                    by_source[source_id] = (ts, row)
        for _, row in sorted(by_source.values(), key=lambda x: x[0]):
            fetched += 1
            refs.append(
                {
                    "source_id": row.get("source_id") or row["sk"].split("#", 2)[-1],
                    "fetch_sk": row["sk"],
                }
            )
    else:
        for row in rows:
            status = row.get("status", "UNKNOWN")
            if status == "FETCHED":
                fetched += 1
            elif status == "NO_NEW":
                no_new += 1
            refs.append(
                {
                    "source_id": row.get("source_id") or row["sk"].split("#", 2)[-1],
                    "fetch_sk": row["sk"],
                }
            )

    return {
        "issue_date": issue_date,
        "user_id": user_id,
        "source_refs": refs,
        "fetch_rows_count": len(refs),
        "fetched_rows_count": fetched,
        "no_new_rows_count": no_new,
        "window_mode": use_window,
    }
