"""
Live scorer only — no backfill of historical picks.

- Entry: same-day close once issue_date < today (Pacific).
- Exits: T+7 and T+30 calendar days when those dates have passed.
- TEST_RUN + allow_test_quote: same-day latest quote for dry runs.
"""
from __future__ import annotations

import os
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import boto3
from boto3.dynamodb.conditions import Attr

from scorerlib.mtm import build_mtm_report
from scorerlib.score import score_pick

TABLE_NAME = os.environ["SENTIMENT_TABLE_NAME"]


def _prefix() -> str:
    if (os.environ.get("TEST_RUN") or "").strip().lower() in ("1", "true", "yes"):
        return "test/"
    return ""


def _dynamo_value(value):
    if isinstance(value, float):
        return Decimal(str(value))
    return value


def _today_pt() -> str:
    tz = os.environ.get("SCHEDULE_LOCAL_TZ", "America/Los_Angeles")
    return datetime.now(ZoneInfo(tz)).date().isoformat()


def _load_finnhub_key() -> str:
    import json

    arn = (os.environ.get("LLM_PANEL_KEYS_SECRET_ARN") or "").strip()
    if arn:
        raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
        return (json.loads(raw).get("finnhub_api_key") or "").strip()
    return (os.environ.get("FINNHUB_API_KEY") or "").strip()


def _iter_pick_items(table, *, issue_date: str | None = None):
    prefix = _prefix()
    scan_kwargs = {
        "FilterExpression": Attr("sk").begins_with(f"{prefix}PICK#"),
    }
    if issue_date:
        scan_kwargs["FilterExpression"] = scan_kwargs["FilterExpression"] & Attr(
            "issue_date"
        ).eq(issue_date)

    while True:
        resp = table.scan(**scan_kwargs)
        for item in resp.get("Items", []):
            yield item
        last = resp.get("LastEvaluatedKey")
        if not last:
            break
        scan_kwargs["ExclusiveStartKey"] = last


def handler(event, context):
    event = event or {}
    if event.get("mtm_report") is True or str(event.get("mtm_report", "")).lower() in (
        "1",
        "true",
        "yes",
    ):
        finnhub = _load_finnhub_key()
        table = boto3.resource("dynamodb").Table(TABLE_NAME)
        today = _today_pt()
        picks = list(_iter_pick_items(table))
        return {"status": "ok", "mtm": build_mtm_report(picks=picks, today=today, finnhub_key=finnhub)}

    issue_date_filter = (event.get("issue_date") or "").strip() or None
    allow_test = event.get("allow_test_quote") is True or str(
        event.get("allow_test_quote", "")
    ).lower() in ("1", "true", "yes")

    finnhub = _load_finnhub_key()
    if not finnhub:
        print("warning: no finnhub_api_key — scoring uses Yahoo Finance only")

    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    today = _today_pt()
    updated = 0
    scanned = 0
    details = []

    for pick in _iter_pick_items(table, issue_date=issue_date_filter):
        scanned += 1
        try:
            changes = score_pick(
                pick=pick,
                finnhub_key=finnhub,
                today=today,
                allow_test_quote=allow_test,
            )
        except Exception as exc:
            print(f"score_pick failed {pick.get('ticker')}: {exc!r}")
            continue
        if not changes:
            continue
        key = {"pk": pick["pk"], "sk": pick["sk"]}
        expr_names = {}
        expr_vals = {}
        parts = []
        for i, (k, v) in enumerate(changes.items()):
            nk, vk = f"#k{i}", f":v{i}"
            expr_names[nk] = k
            expr_vals[vk] = _dynamo_value(v)
            parts.append(f"{nk} = {vk}")
        table.update_item(
            Key=key,
            UpdateExpression="SET " + ", ".join(parts),
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_vals,
        )
        updated += 1
        details.append(
            {
                "ticker": pick.get("ticker"),
                "model_id": pick.get("model_id"),
                "issue_date": pick.get("issue_date"),
                "changes": list(changes.keys()),
            }
        )

    return {
        "status": "ok",
        "today_pt": today,
        "scanned": scanned,
        "updated": updated,
        "allow_test_quote": allow_test,
        "test_run": bool(_prefix()),
        "details": details[:40],
    }
