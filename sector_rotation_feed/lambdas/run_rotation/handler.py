"""Daily sector rotation report: compute vs SPY, post to Discord."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import boto3

from rotationlib.analyze import build_report, latest_us_market_date
from rotationlib.discord_format import format_messages

ET = ZoneInfo("America/New_York")
DISCORD_API = "https://discord.com/api/v10"
USER_AGENT = "sector-rotation-feed/1.0 (+aws-lambda)"
TABLE_NAME = (os.environ.get("ROTATION_TABLE_NAME") or "").strip()
DISCORD_BOT_SECRET_ARN = (os.environ.get("DISCORD_BOT_SECRET_ARN") or "").strip()
DISCORD_WEBHOOK_SECRET_ARN = (os.environ.get("DISCORD_WEBHOOK_SECRET_ARN") or "").strip()
CHANNEL_ID_ENV = (os.environ.get("SECTOR_ROTATION_DISCORD_CHANNEL_ID") or "").strip()


def _floats_to_decimal(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _floats_to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_floats_to_decimal(i) for i in obj]
    return obj


def _save_report(report: dict) -> None:
    if not TABLE_NAME:
        return
    td = report.get("trade_date", "")
    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    table.put_item(
        Item=_floats_to_decimal(
            {
                "pk": f"ISSUE#{td}",
                "sk": "REPORT",
                "trade_date": td,
                "created_at": datetime.now(tz=ET).isoformat(),
                "payload": report,
            }
        )
    )


def _get_bot_credentials() -> tuple[str, str]:
    if not DISCORD_BOT_SECRET_ARN:
        return "", ""
    raw = (
        boto3.client("secretsmanager")
        .get_secret_value(SecretId=DISCORD_BOT_SECRET_ARN)
        .get("SecretString")
        or ""
    ).strip()
    if not raw:
        return "", ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "", ""
    token = (data.get("bot_token") or "").strip()
    channel_id = str(data.get("channel_id") or CHANNEL_ID_ENV or "").strip()
    return token, channel_id


def _get_webhook_url() -> str:
    if not DISCORD_WEBHOOK_SECRET_ARN:
        return ""
    raw = (
        boto3.client("secretsmanager")
        .get_secret_value(SecretId=DISCORD_WEBHOOK_SECRET_ARN)
        .get("SecretString")
        or ""
    ).strip()
    if not raw:
        return ""
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        return (data.get("webhook_url") or "").strip()
    m = re.search(r"https://discord\.com/api/webhooks/\S+", raw)
    return (m.group(0) if m else raw).strip()


def _http_json(url: str, *, method: str, headers: dict, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read().decode("utf-8", errors="replace").strip()
        return json.loads(raw) if raw else {}


def _bot_post(token: str, channel_id: str, content: str) -> None:
    _http_json(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bot {token}",
            "User-Agent": USER_AGENT,
        },
        body={"content": content},
    )


def _post_webhook(webhook_url: str, content: str) -> None:
    post_url = webhook_url + ("&" if "?" in webhook_url else "?") + "wait=true"
    req = urllib.request.Request(
        post_url,
        data=json.dumps({"content": content}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        resp.read()


def handler(event, context):
    event = event or {}
    if (os.environ.get("SKIP_DISCORD_PUBLISH") or "").lower() in ("1", "true", "yes"):
        dry = True
    else:
        dry = False

    trade_date_s = (event.get("trade_date") or "").strip()
    if trade_date_s:
        from datetime import date

        td = date.fromisoformat(trade_date_s)
    else:
        td = latest_us_market_date()

    report = build_report(trade_date=td)
    _save_report(report)
    messages = format_messages(report)

    if dry:
        return {
            "trade_date": report["trade_date"],
            "discord_publish_status": "skipped_env",
            "message_parts": len(messages),
            "in_count": len(report.get("in_sectors") or []),
        }

    token, channel_id = _get_bot_credentials()
    webhook = _get_webhook_url()
    if not token and not webhook:
        return {
            "trade_date": report["trade_date"],
            "discord_publish_status": "skipped_no_credentials",
            "message_parts": len(messages),
        }

    status = "error"
    try:
        if token and channel_id:
            for msg in messages:
                _bot_post(token, channel_id, msg)
            status = "posted_bot"
        elif webhook:
            for msg in messages:
                _post_webhook(webhook, msg)
            status = "posted_webhook"
        else:
            status = "skipped_no_channel"
    except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
        print(f"discord publish failed: {exc!r}")
        status = "error"

    return {
        "trade_date": report["trade_date"],
        "discord_publish_status": status,
        "message_parts": len(messages),
        "in_count": len(report.get("in_sectors") or []),
        "out_count": len(report.get("out_sectors") or []),
    }
