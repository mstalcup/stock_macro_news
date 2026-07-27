"""Post daily LLM sentiment panel picks to Discord."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

import boto3
from boto3.dynamodb.conditions import Key

from discord_insights import format_llm_performance_messages, format_signal_matrix_messages

TABLE_NAME = os.environ["SENTIMENT_TABLE_NAME"]
DISCORD_BOT_SECRET_ARN = (os.environ.get("DISCORD_BOT_SECRET_ARN") or "").strip()
DISCORD_WEBHOOK_SECRET_ARN = (os.environ.get("DISCORD_WEBHOOK_SECRET_ARN") or "").strip()
SENTIMENT_CHANNEL_ID = (os.environ.get("SENTIMENT_DISCORD_CHANNEL_ID") or "").strip()

# Keep in sync with query_llm_panel/sentimentlib/config.py PANEL_MODELS
ACTIVE_MODEL_IDS = (
    "openai-gpt-4o",
    "gemini-2.5-flash",
    "grok-4.3",
)

DISCORD_API = "https://discord.com/api/v10"
USER_AGENT = "llm-sentiment-feed/1.0 (+aws-lambda)"
MSG_LIMIT = 1900


def _prefix() -> str:
    if (os.environ.get("TEST_RUN") or "").strip().lower() in ("1", "true", "yes"):
        return "test/"
    return ""


def _active_model_ids() -> tuple[str, ...]:
    env = (os.environ.get("PUBLISH_MODEL_IDS") or "").strip()
    if env:
        return tuple(m.strip() for m in env.split(",") if m.strip())
    return ACTIVE_MODEL_IDS


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
    channel_id = str(data.get("channel_id") or SENTIMENT_CHANNEL_ID or "").strip()
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


def _chunk_text(text: str, limit: int = MSG_LIMIT) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= limit:
        return [t]
    out: list[str] = []
    while t:
        if len(t) <= limit:
            out.append(t)
            break
        cut = t.rfind("\n\n", 0, limit)
        if cut < limit // 3:
            cut = t.rfind("\n", 0, limit)
        if cut < limit // 3:
            cut = limit
        out.append(t[:cut].rstrip())
        t = t[cut:].lstrip()
    return out


def _http_json(url: str, *, method: str, headers: dict, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read().decode("utf-8", errors="replace").strip()
        return json.loads(raw) if raw else {}


def _bot_post(token: str, channel_id: str, content: str) -> dict:
    return _http_json(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bot {token}",
            "User-Agent": USER_AGENT,
        },
        body={"content": content},
    )


def _post_webhook(webhook_url: str, content: str) -> dict:
    post_url = webhook_url + ("&" if "?" in webhook_url else "?") + "wait=true"
    req = urllib.request.Request(
        post_url,
        data=json.dumps({"content": content}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read().decode("utf-8", errors="replace").strip()
        return json.loads(raw) if raw else {}


def _sanitize_error(err: str) -> str:
    s = (err or "").strip()
    s = re.sub(r"key=[A-Za-z0-9_-]+", "key=***", s, flags=re.I)
    s = re.sub(r"https?://\S+", "", s)
    s = re.sub(r"\s+", " ", s).strip()
    if "HTTP 404" in s and "gemini" in s.lower():
        return "Gemini model unavailable (retired model slug)."
    if "HTTP 429" in s:
        return "API quota exceeded — check billing."
    if "HTTP 400" in s and "Model not found" in s:
        return "Model slug not found on provider API."
    if "HTTP 400" in s:
        return "Bad request to provider API."
    if len(s) > 140:
        s = s[:137] + "..."
    return s or "Provider error"


def _load_models(issue_date: str) -> list[dict]:
    allowed = _active_model_ids()
    allowed_set = frozenset(allowed)
    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    pk = f"{_prefix()}ISSUE#{issue_date}"
    resp = table.query(KeyConditionExpression=Key("pk").eq(pk))
    items = [
        i
        for i in resp.get("Items", [])
        if str(i.get("sk", "")).startswith("MODEL#")
        and (i.get("model_id") or "") in allowed_set
    ]

    by_id: dict[str, dict] = {}
    for item in items:
        mid = item.get("model_id") or ""
        prev = by_id.get(mid)
        if not prev:
            by_id[mid] = item
            continue
        if item.get("status") == "ok" and prev.get("status") != "ok":
            by_id[mid] = item
            continue
        if (item.get("queried_at") or "") > (prev.get("queried_at") or ""):
            by_id[mid] = item

    return [by_id[mid] for mid in allowed if mid in by_id]


def _format_pick(p: dict) -> str:
    direction = (p.get("direction") or "?").upper()
    ticker = p.get("ticker") or "?"
    conv = p.get("conviction") or "?"
    rationale = (p.get("rationale") or "").strip()
    catalysts = (p.get("catalysts") or "").strip()
    lines = [f"• **{ticker}** | **{direction}** | {conv} conviction"]
    if rationale:
        lines.append(rationale)
    if catalysts:
        lines.append(f"_Catalysts:_ {catalysts}")
    return "\n".join(lines)


def _format_model_block(item: dict) -> str:
    mid = item.get("model_id") or "unknown"
    bias = item.get("market_bias") or "?"
    status = item.get("status") or "?"
    lines = [f"**{mid}** · market bias: `{bias}`"]

    if status != "ok":
        lines.append(f"⚠ {_sanitize_error(item.get('error') or '')}")
        return "\n".join(lines)

    picks = item.get("picks") or []
    if not picks:
        lines.append("_(no picks returned)_")
        return "\n".join(lines)

    for p in picks:
        lines.append("")
        lines.append(_format_pick(p))
    return "\n".join(lines)


def _format_messages(*, issue_date: str, macro_slot: str, models: list[dict]) -> list[str]:
    slot_label = "pre-open" if macro_slot == "pre_open" else macro_slot.replace("_", "-")
    header = (
        f"**LLM sentiment panel — {issue_date}** ({slot_label})\n"
        "_30-calendar-day paper positions (long/short) for live tracking — not investment advice._"
    )
    messages: list[str] = list(_chunk_text(header))

    if not models:
        messages.extend(_chunk_text("_No panel results for active models today._"))
        return messages

    for item in models:
        block = _format_model_block(item)
        messages.extend(_chunk_text(block))

    return messages


def handler(event, context):
    event = event or {}
    issue_date = (event.get("issue_date") or "").strip()
    macro_slot = (event.get("macro_slot") or "pre_open").strip()

    if (os.environ.get("SKIP_DISCORD_PUBLISH") or "").lower() in ("1", "true", "yes"):
        return {
            "issue_date": issue_date,
            "macro_slot": macro_slot,
            "discord_publish_status": "skipped_env",
        }

    if not issue_date:
        return {
            "issue_date": issue_date,
            "macro_slot": macro_slot,
            "discord_publish_status": "skipped_no_issue_date",
        }

    models = _load_models(issue_date)
    messages = _format_messages(issue_date=issue_date, macro_slot=macro_slot, models=models)
    try:
        messages.extend(format_signal_matrix_messages(issue_date=issue_date, slot=macro_slot))
    except Exception as exc:
        print(f"signal matrix block skipped: {exc!r}")
        messages.extend(_chunk_text(f"**Signal confluence — {issue_date}**\n_(unavailable: {exc})_"))
    try:
        messages.extend(format_llm_performance_messages(issue_date=issue_date, table_name=TABLE_NAME))
    except Exception as exc:
        print(f"llm performance block skipped: {exc!r}")

    token, channel_id = _get_bot_credentials()
    webhook = _get_webhook_url()
    if not token and not webhook:
        return {
            "issue_date": issue_date,
            "macro_slot": macro_slot,
            "discord_publish_status": "skipped_no_credentials",
            "model_count": len(models),
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
        "issue_date": issue_date,
        "macro_slot": macro_slot,
        "discord_publish_status": status,
        "model_count": len(models),
        "message_parts": len(messages),
    }
