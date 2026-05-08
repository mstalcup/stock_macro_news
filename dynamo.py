"""
shared/dynamo.py — DynamoDB helpers shared across all Lambda functions.
"""
import os
import json
import time
import boto3
from datetime import datetime, timezone
from decimal import Decimal

from .config import DYNAMO_TABLE, DYNAMO_TTL_DAYS


def _get_table():
    region = os.environ.get("AWS_REGION", "us-east-1")
    dynamo = boto3.resource("dynamodb", region_name=region)
    return dynamo.Table(os.environ.get("DYNAMODB_TABLE", DYNAMO_TABLE))


def _ttl_timestamp() -> int:
    """Unix timestamp 90 days from now."""
    return int(time.time()) + (DYNAMO_TTL_DAYS * 86400)


def _floats_to_decimal(obj):
    """
    DynamoDB doesn't accept Python floats — recursively convert to Decimal.
    """
    if isinstance(obj, float):
        return Decimal(str(obj))
    elif isinstance(obj, dict):
        return {k: _floats_to_decimal(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_floats_to_decimal(i) for i in obj]
    return obj


def _decimal_to_float(obj):
    """Reverse: Decimal → float for JSON serialisation."""
    if isinstance(obj, Decimal):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: _decimal_to_float(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [_decimal_to_float(i) for i in obj]
    return obj


def save_report(date_str: str, sort_key: str, payload: dict) -> None:
    """
    Write a report record to DynamoDB.

    Args:
        date_str:  'YYYY-MM-DD'
        sort_key:  one of SK_RAW_DATA | SK_SIGNALS | SK_NEWSLETTER
        payload:   dict to store (floats will be converted to Decimal)
    """
    table = _get_table()
    item = {
        "date":        date_str,
        "report_type": sort_key,
        "created_at":  datetime.now(timezone.utc).isoformat(),
        "ttl":         _ttl_timestamp(),
        **_floats_to_decimal(payload),
    }
    table.put_item(Item=item)
    print(f"[DynamoDB] Saved {sort_key} for {date_str}")


def load_report(date_str: str, sort_key: str) -> dict | None:
    """
    Read a report record from DynamoDB.

    Returns the item dict (with Decimals converted back to float),
    or None if not found.
    """
    table = _get_table()
    resp = table.get_item(Key={"date": date_str, "report_type": sort_key})
    item = resp.get("Item")
    if item:
        return _decimal_to_float(item)
    return None


def list_dates(limit: int = 30) -> list[str]:
    """
    Scan for the most recent `limit` date keys (expensive on large tables —
    fine for our small dataset).
    """
    table = _get_table()
    resp = table.scan(
        ProjectionExpression="#d",
        ExpressionAttributeNames={"#d": "date"},
        Limit=limit * 3,   # over-fetch to account for multiple sort keys
    )
    dates = sorted({item["date"] for item in resp.get("Items", [])}, reverse=True)
    return dates[:limit]
