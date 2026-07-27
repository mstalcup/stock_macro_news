"""
Post macro digest from S3 to Discord (bot preferred, webhook fallback).

Reads digest.json (+ optional headline thread from deduped.json).
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

import boto3

BUCKET = os.environ["NEWS_ARTIFACTS_BUCKET"]
DISCORD_BOT_SECRET_ARN = (os.environ.get("DISCORD_BOT_SECRET_ARN") or "").strip()
DISCORD_WEBHOOK_SECRET_ARN = (os.environ.get("DISCORD_WEBHOOK_SECRET_ARN") or "").strip()
DISCORD_PUBLISH_SLOTS = (os.environ.get("DISCORD_PUBLISH_SLOTS") or "pre_open").strip()
MACRO_CHANNEL_ID = (os.environ.get("MACRO_DISCORD_CHANNEL_ID") or "").strip()

DISCORD_API = "https://discord.com/api/v10"
USER_AGENT = "stock-macro-news-bot/1.0 (+aws-lambda)"
SLOT_LABELS = {
    "pre_open": "Morning briefing (pre-open)",
    "pre_close": "Afternoon recap (pre-close)",
}


def _publish_slots() -> frozenset[str]:
    return frozenset(s.strip() for s in DISCORD_PUBLISH_SLOTS.split(",") if s.strip())


def _get_bot_credentials() -> tuple[str, str]:
    if not DISCORD_BOT_SECRET_ARN:
        return "", ""
    sm = boto3.client("secretsmanager")
    raw = (sm.get_secret_value(SecretId=DISCORD_BOT_SECRET_ARN).get("SecretString") or "").strip()
    if not raw:
        return "", ""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return "", ""
    token = (data.get("bot_token") or "").strip()
    channel_id = str(data.get("channel_id") or MACRO_CHANNEL_ID or "").strip()
    return token, channel_id


def _get_webhook_url() -> str:
    if not DISCORD_WEBHOOK_SECRET_ARN:
        return ""
    sm = boto3.client("secretsmanager")
    raw = (sm.get_secret_value(SecretId=DISCORD_WEBHOOK_SECRET_ARN).get("SecretString") or "").strip()
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


def _chunks(text: str, limit: int = 1900) -> list[str]:
    t = (text or "").strip()
    out: list[str] = []
    while t:
        out.append(t[:limit])
        t = t[limit:].lstrip()
    return out


def _load_s3_json(key: str) -> dict | None:
    if not key:
        return None
    try:
        body = boto3.client("s3").get_object(Bucket=BUCKET, Key=key)["Body"].read()
        return json.loads(body)
    except Exception as exc:
        print(f"publish_discord s3 read failed {key}: {exc!r}")
        return None


def _build_messages(*, issue_date: str, slot: str, digest_doc: dict, deduped_doc: dict | None) -> list[str]:
    slot_label = SLOT_LABELS.get(slot, slot)
    markdown = (digest_doc.get("digest_markdown") or "").strip()
    if not markdown:
        digest = digest_doc.get("digest") or {}
        markdown = json.dumps(digest, indent=2)[:1800]

    header = f"**Macro news — {issue_date}** ({slot_label})\n\n"
    messages = _chunks(header + markdown)

    if deduped_doc:
        lines = ["**Headlines**"]
        for i, art in enumerate((deduped_doc.get("articles") or [])[:12], 1):
            title = (art.get("title") or "").strip()[:120]
            url = (art.get("canonical_url") or art.get("url") or "").strip()
            if url:
                lines.append(f"{i}. {title}\n{url}")
            else:
                lines.append(f"{i}. {title}")
        thread_body = "\n".join(lines)
        messages.extend(_chunks(thread_body))

    return messages


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


def _bot_thread(token: str, channel_id: str, message_id: str, name: str) -> dict:
    return _http_json(
        f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}/threads",
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bot {token}",
            "User-Agent": USER_AGENT,
        },
        body={"name": name[:90], "auto_archive_duration": 1440},
    )


def _post_webhook(webhook_url: str, content: str, message_reference: dict | None = None) -> dict:
    payload: dict = {"content": content}
    if message_reference:
        payload["message_reference"] = message_reference
    post_url = webhook_url + ("&" if "?" in webhook_url else "?") + "wait=true"
    req = urllib.request.Request(
        post_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": USER_AGENT},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        raw = resp.read().decode("utf-8", errors="replace").strip()
        return json.loads(raw) if raw else {}


def _publish_bot(token: str, channel_id: str, issue_date: str, slot: str, messages: list[str]) -> str:
    root = _bot_post(token, channel_id, messages[0])
    root_id = (root.get("id") or "").strip()
    if not root_id:
        raise RuntimeError("Discord bot post returned no message id")
    thread_name = f"Headlines — {issue_date} {slot}"
    target = channel_id
    if len(messages) > 1:
        try:
            thread = _bot_thread(token, channel_id, root_id, thread_name)
            target = (thread.get("id") or "").strip() or channel_id
        except Exception as exc:
            print(f"discord thread create failed: {exc!r}")
        for msg in messages[1:]:
            _bot_post(token, target, msg)
    return "posted_thread" if target != channel_id else "posted_channel"


def _publish_webhook(webhook_url: str, messages: list[str]) -> str:
    root = _post_webhook(webhook_url, messages[0])
    root_id = (root.get("id") or "").strip()
    channel_id = (root.get("channel_id") or "").strip()
    for msg in messages[1:]:
        ref = {"message_id": root_id, "fail_if_not_exists": False} if root_id else None
        if ref and channel_id:
            ref["channel_id"] = channel_id
        try:
            _post_webhook(webhook_url, msg, ref)
        except Exception:
            _post_webhook(webhook_url, msg)
    return "posted_webhook"


def handler(event, context):
    event = event or {}
    issue_date = (event.get("issue_date") or "").strip()
    slot = (event.get("slot") or "").strip()
    digest_key = (event.get("digest_s3_key") or "").strip()
    deduped_key = (event.get("deduped_s3_key") or "").strip()

    if slot not in _publish_slots():
        return {
            "issue_date": issue_date,
            "slot": slot,
            "discord_publish_status": "skipped_slot",
        }

    skip = (os.environ.get("SKIP_DISCORD_PUBLISH") or "").lower() in ("1", "true", "yes")
    if skip:
        return {"issue_date": issue_date, "slot": slot, "discord_publish_status": "skipped_env"}

    if not digest_key and issue_date and slot:
        digest_key = f"v1/date={issue_date}/slot={slot}/digest.json"
    if not deduped_key and issue_date and slot:
        deduped_key = f"v1/date={issue_date}/slot={slot}/deduped.json"

    digest_doc = _load_s3_json(digest_key)
    if not digest_doc:
        return {
            "issue_date": issue_date,
            "slot": slot,
            "discord_publish_status": "skipped_no_digest",
        }

    deduped_doc = _load_s3_json(deduped_key)
    messages = _build_messages(
        issue_date=issue_date or digest_doc.get("issue_date", ""),
        slot=slot or digest_doc.get("slot", ""),
        digest_doc=digest_doc,
        deduped_doc=deduped_doc,
    )

    token, channel_id = _get_bot_credentials()
    webhook = _get_webhook_url()
    if not token and not webhook:
        return {
            "issue_date": issue_date,
            "slot": slot,
            "discord_publish_status": "skipped_no_credentials",
        }

    status = "error"
    try:
        if token and channel_id:
            try:
                status = _publish_bot(
                    token,
                    channel_id,
                    issue_date or digest_doc.get("issue_date", ""),
                    slot or digest_doc.get("slot", ""),
                    messages,
                )
            except Exception as exc:
                print(f"bot publish failed: {exc!r}")
                if webhook:
                    status = _publish_webhook(webhook, messages)
                else:
                    raise
        else:
            status = _publish_webhook(webhook, messages)
    except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
        print(f"discord publish failed: {exc!r}")
        status = "error"

    return {
        "issue_date": issue_date,
        "slot": slot,
        "discord_publish_status": status,
        "digest_s3_key": digest_key,
    }
