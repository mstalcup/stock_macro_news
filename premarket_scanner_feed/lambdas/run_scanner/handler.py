"""Premarket gappers + Trend Join Long Lambda handler."""
from __future__ import annotations

import json
import logging
import os
import re
import urllib.error
import urllib.request
from datetime import datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import boto3

from scannerlib.discord_format import format_gappers_messages, format_tjl_messages
from scannerlib.gappers import one_line_summary, scan_gappers
from scannerlib.tjl import scan_tjl
from scannerlib.yahoo import market_issue_date

LOG = logging.getLogger(__name__)
logging.getLogger().setLevel(logging.INFO)

ET = ZoneInfo("America/New_York")
DISCORD_API = "https://discord.com/api/v10"
TABLE_NAME = (os.environ.get("SCANNER_TABLE_NAME") or "").strip()
DISCORD_BOT_SECRET_ARN = (os.environ.get("DISCORD_BOT_SECRET_ARN") or "").strip()
DISCORD_WEBHOOK_SECRET_ARN = (os.environ.get("DISCORD_WEBHOOK_SECRET_ARN") or "").strip()
CHANNEL_ID_ENV = (os.environ.get("SCANNER_DISCORD_CHANNEL_ID") or "").strip()
NEWS_KEYS_SECRET_ARN = (os.environ.get("NEWS_KEYS_SECRET_ARN") or "").strip()


def _floats_to_decimal(obj: Any) -> Any:
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _floats_to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_floats_to_decimal(i) for i in obj]
    return obj


def _save(issue_date: str, sk: str, payload: dict[str, Any]) -> None:
    if not TABLE_NAME:
        return
    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    table.put_item(
        Item=_floats_to_decimal(
            {
                "pk": f"ISSUE#{issue_date}",
                "sk": sk,
                "issue_date": issue_date,
                "created_at": datetime.now(tz=ET).isoformat(),
                "payload": payload,
            }
        )
    )


def _load(issue_date: str, sk: str) -> dict[str, Any] | None:
    if not TABLE_NAME:
        return None
    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    resp = table.get_item(Key={"pk": f"ISSUE#{issue_date}", "sk": sk})
    item = resp.get("Item") or {}
    payload = item.get("payload")
    return payload if isinstance(payload, dict) else None


def _load_finnhub_key() -> str:
    if not NEWS_KEYS_SECRET_ARN:
        return (os.environ.get("FINNHUB_API_KEY") or "").strip()
    raw = (
        boto3.client("secretsmanager")
        .get_secret_value(SecretId=NEWS_KEYS_SECRET_ARN)
        .get("SecretString")
        or ""
    ).strip()
    if not raw:
        return ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return ""
    return (data.get("finnhub_api_key") or "").strip()


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


def _post_discord(messages: list[str]) -> None:
    token, channel_id = _get_bot_credentials()
    webhook = _get_webhook_url()
    for content in messages:
        if token and channel_id:
            req = urllib.request.Request(
                f"{DISCORD_API}/channels/{channel_id}/messages",
                data=json.dumps({"content": content}).encode("utf-8"),
                headers={
                    "Authorization": f"Bot {token}",
                    "Content-Type": "application/json",
                    "User-Agent": "premarket-scanner-feed/1.0",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=25) as resp:
                    resp.read()
                continue
            except urllib.error.HTTPError as exc:
                LOG.warning("Discord bot post failed %s: %s", exc.code, exc.read()[:200])
        if webhook:
            req = urllib.request.Request(
                webhook,
                data=json.dumps({"content": content}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=25) as resp:
                resp.read()
        else:
            LOG.info("No Discord credentials — message:\n%s", content)


def _run_gappers(event: dict[str, Any]) -> dict[str, Any]:
    key = _load_finnhub_key()
    payload = scan_gappers(
        finnhub_key=key,
        min_gap=float(event.get("min_gap") or os.environ.get("MIN_GAP_PCT") or 5),
        min_price=float(event.get("min_price") or os.environ.get("MIN_PRICE") or 3),
        min_volume=float(event.get("min_volume") or os.environ.get("MIN_VOLUME") or 50000),
        top_n=int(event.get("top_n") or os.environ.get("TOP_N") or 10),
    )
    _save(payload["issue_date"], "GAPPERS", payload)
    summary = one_line_summary(payload)
    LOG.info(summary)
    if not event.get("skip_discord"):
        _post_discord(format_gappers_messages(payload))
    return {"mode": "gappers", "summary": summary, "payload": payload}


def _run_tjl(event: dict[str, Any]) -> dict[str, Any]:
    issue_date = (event.get("issue_date") or market_issue_date()).strip()
    symbols = event.get("symbols")
    if not symbols:
        gappers = _load(issue_date, "GAPPERS") or {}
        symbols = [g["symbol"] for g in (gappers.get("gappers") or []) if g.get("symbol")]
    if event.get("demo_symbols"):
        symbols = list(event["demo_symbols"])
    force = bool(event.get("force") or event.get("force_window"))
    payload = scan_tjl(symbols or [], force=force)
    if not payload.get("error"):
        _save(payload.get("issue_date") or issue_date, "TJL", payload)
    for row in payload.get("all_results") or []:
        LOG.info("%s: %s — %s", row.get("symbol"), row.get("result"), row.get("reason"))
    if not event.get("skip_discord"):
        _post_discord(format_tjl_messages(payload))
    return {"mode": "tjl", "payload": payload}


def handler(event: dict[str, Any] | None = None, context: Any = None) -> dict[str, Any]:
    event = event or {}
    mode = (event.get("mode") or "gappers").strip().lower()
    LOG.info("premarket scanner mode=%s", mode)
    if mode in ("gappers", "gap", "premarket"):
        result = _run_gappers(event)
    elif mode in ("tjl", "trend_join", "trend"):
        result = _run_tjl(event)
    elif mode == "both":
        g = _run_gappers({**event, "skip_discord": event.get("skip_discord")})
        t = _run_tjl({**event, "skip_discord": event.get("skip_discord"), "force": True})
        result = {"mode": "both", "gappers": g, "tjl": t}
    else:
        raise ValueError(f"Unknown mode: {mode}")
    return {"statusCode": 200, "body": json.dumps(result, default=str)}
