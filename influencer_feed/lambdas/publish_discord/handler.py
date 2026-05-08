import json
import os
import re
import urllib.error
import urllib.request

import boto3

TABLE_NAME = os.environ["TABLE_NAME"]
DISCORD_SECRET_ARN = (os.environ.get("DISCORD_SECRET_ARN") or "").strip()
DISCORD_BOT_SECRET_ARN = (os.environ.get("DISCORD_BOT_SECRET_ARN") or "").strip()

DISCORD_API = "https://discord.com/api/v10"
USER_AGENT = "stock-macro-news-bot/1.0 (+aws-lambda)"


def _feed_overlay(root: dict, user_id: str) -> dict:
    """Optional per-feed overrides: root['feeds'][user_id] merges over root for discord fields."""
    feeds = root.get("feeds")
    if not isinstance(feeds, dict):
        return {}
    block = feeds.get(user_id)
    return block if isinstance(block, dict) else {}


def _get_webhook_url(user_id: str) -> str:
    if not DISCORD_SECRET_ARN:
        return ""
    sm = boto3.client("secretsmanager")
    raw = (sm.get_secret_value(SecretId=DISCORD_SECRET_ARN).get("SecretString") or "").strip()
    if not raw:
        return ""
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = {}
        overlay = _feed_overlay(data, user_id)
        url = (overlay.get("webhook_url") or data.get("webhook_url") or "").strip()
        if url:
            return url
        m = re.search(r"https://discord\.com/api/webhooks/\S+", raw)
        return (m.group(0) if m else "").strip()
    return raw


def _get_bot_credentials(user_id: str) -> tuple[str, str]:
    if not DISCORD_BOT_SECRET_ARN:
        return "", ""
    sm = boto3.client("secretsmanager")
    raw = (sm.get_secret_value(SecretId=DISCORD_BOT_SECRET_ARN).get("SecretString") or "").strip()
    if not raw:
        return "", ""
    data: dict = {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        m_tok = re.search(r"bot_token\s*[:=]\s*\"?([A-Za-z0-9._\-]+)\"?", raw)
        m_chan = re.search(r"channel_id\s*[:=]\s*\"?(\d+)\"?", raw)
        data = {}
        if m_tok:
            data["bot_token"] = m_tok.group(1)
        if m_chan:
            data["channel_id"] = m_chan.group(1)
    overlay = _feed_overlay(data, user_id)
    token = (overlay.get("bot_token") or data.get("bot_token") or "").strip()
    channel_id = str(overlay.get("channel_id") or data.get("channel_id") or "").strip()
    return token, channel_id


def _http_json(url: str, *, method: str, headers: dict, body: dict | None = None) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read().decode("utf-8", errors="replace").strip()
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"Discord HTTP {exc.code} {method} {url}: {err_body}") from exc


def _bot_post_message(token: str, channel_id: str, content: str) -> dict:
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bot {token}",
        "User-Agent": USER_AGENT,
    }
    return _http_json(url, method="POST", headers=headers, body={"content": content})


def _bot_create_thread(token: str, channel_id: str, message_id: str, name: str) -> dict:
    url = f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}/threads"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bot {token}",
        "User-Agent": USER_AGENT,
    }
    return _http_json(
        url,
        method="POST",
        headers=headers,
        body={"name": name[:90] or "thread", "auto_archive_duration": 1440},
    )


def _post_webhook(webhook_url: str, content: str, message_reference: dict | None = None) -> dict:
    payload = {"content": content}
    if message_reference:
        payload["message_reference"] = message_reference
    body = json.dumps(payload).encode("utf-8")
    post_url = webhook_url + ("&" if "?" in webhook_url else "?") + "wait=true"
    req = urllib.request.Request(
        post_url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": USER_AGENT,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status >= 300:
                raise RuntimeError(f"Discord webhook HTTP {resp.status}")
            raw = resp.read().decode("utf-8", errors="replace").strip()
            if not raw:
                return {}
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return {}
    except urllib.error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")[:2000]
        raise RuntimeError(f"Discord webhook HTTP {exc.code}: {err_body}") from exc


def _discord_content_chunks(text: str, limit: int = 1900) -> list[str]:
    """Discord message content max ~2000; stay under limit per chunk."""
    t = (text or "").strip()
    if not t:
        return []
    chunks: list[str] = []
    while t:
        chunks.append(t[:limit])
        t = t[limit:].lstrip()
    return chunks


def _build_messages(issue_date: str, issue: dict, source_rows: list[dict], user_id: str) -> list[str]:
    messages = []
    title = "Crypto Digest" if user_id == "crypto" else "Macro Digest"
    lines = [f"**{title} — {issue_date}**", ""]
    top = (issue.get("global_summary_smol", "") or "").strip()
    if not top or top.lower().startswith("no source rows found"):
        top = "No actionable updates in this window."
    lines.append(top)
    catalysts = (issue.get("global_catalysts") or "").strip()
    if catalysts:
        lines.extend(["", "**Catalysts / dates to watch**", catalysts])
    technical = (issue.get("global_technical") or "").strip()
    if technical:
        lines.extend(["", "**Levels / TA snapshot**", technical])
    shift = (issue.get("global_summary_shift") or "").strip()
    if shift:
        lines.extend(["", f"**What changed:** {shift}"])
    ticker_focus = issue.get("global_ticker_focus") or []
    if ticker_focus:
        lines.extend(["", "**Ticker Focus**"])
        for t in ticker_focus[:12]:
            if not isinstance(t, dict):
                continue
            ticker = (t.get("ticker") or "").strip().upper()
            if not ticker:
                continue
            consensus = (t.get("consensus") or "mixed").strip().lower()
            note = (t.get("note") or "").strip()
            lines.append(f"- **{ticker}** ({consensus}) — {note}")
    messages.extend(_discord_content_chunks("\n".join(lines)))

    fetched_rows = [r for r in source_rows if r.get("status") == "FETCHED"]
    for row in sorted(fetched_rows, key=lambda r: r.get("display_name", "")):
        name = row.get("display_name") or row.get("source_id", "unknown")
        summary = (row.get("source_summary") or "").strip()
        tickers = row.get("source_tickers") or []
        ticker_text = ", ".join(
            [f"{t.get('ticker','').upper()}({(t.get('direction') or 'unclear')})" for t in tickers if isinstance(t, dict)]
        )
        links = [u for u in (row.get("source_links") or []) if isinstance(u, str) and u.startswith("http")]
        src_lines = [f"**{name}**", summary]
        if ticker_text:
            src_lines.append(f"Tickers: {ticker_text}")
        if links:
            src_lines.append(f"Link: {links[0]}")
        block = "\n".join(src_lines).strip()
        while len(block) > 1900:
            messages.append(block[:1900])
            block = block[1900:]
        if block:
            messages.append(block)
    return messages


def _publish_via_bot(
    token: str, channel_id: str, issue_date: str, messages: list[str], user_id: str
) -> str:
    root = _bot_post_message(token, channel_id, messages[0])
    root_id = (root.get("id") or "").strip() if isinstance(root, dict) else ""
    if not root_id:
        raise RuntimeError("Discord bot post returned no message id")

    thread_name = f"Crypto source notes — {issue_date}" if user_id == "crypto" else f"Source notes — {issue_date}"
    thread_id = ""
    try:
        thread = _bot_create_thread(token, channel_id, root_id, thread_name)
        thread_id = (thread.get("id") or "").strip() if isinstance(thread, dict) else ""
    except RuntimeError as exc:
        print(f"discord bot thread create failed: {exc!r}")

    target_channel = thread_id or channel_id
    for msg in messages[1:]:
        try:
            _bot_post_message(token, target_channel, msg)
        except RuntimeError as exc:
            print(f"discord bot post failed: {exc!r}")

    return "posted_thread" if thread_id else "posted_channel"


def _publish_via_webhook(webhook_url: str, messages: list[str]) -> str:
    root = _post_webhook(webhook_url, messages[0])
    root_id = (root.get("id") or "").strip() if isinstance(root, dict) else ""
    channel_id = (root.get("channel_id") or "").strip() if isinstance(root, dict) else ""
    for msg in messages[1:]:
        if root_id:
            try:
                ref = {"message_id": root_id, "fail_if_not_exists": False}
                if channel_id:
                    ref["channel_id"] = channel_id
                _post_webhook(webhook_url, msg, message_reference=ref)
                continue
            except RuntimeError as reply_exc:
                print(f"discord reply publish fallback: {reply_exc!r}")
        _post_webhook(webhook_url, msg)
    return "posted_webhook"


def handler(event, context):
    issue_date = event["issue_date"]
    user_id = event.get("user_id", "default")
    issue_sk = event.get("issue_sk", f"ISSUE#{issue_date}")
    pk = f"USER#{user_id}"

    skip = (os.environ.get("SKIP_DISCORD_PUBLISH") or "").strip().lower() in ("1", "true", "yes")
    if skip:
        return {
            "status": event.get("status", "COMPOSED"),
            "issue_date": issue_date,
            "user_id": user_id,
            "issue_sk": issue_sk,
            "discord_publish_status": "skipped_env",
        }

    bot_token, bot_channel = _get_bot_credentials(user_id)
    webhook_url = _get_webhook_url(user_id)

    if not bot_token and not webhook_url:
        return {
            "status": event.get("status", "COMPOSED"),
            "issue_date": issue_date,
            "user_id": user_id,
            "issue_sk": issue_sk,
            "discord_publish_status": "skipped_no_credentials",
        }

    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    issue = table.get_item(Key={"pk": pk, "sk": issue_sk}).get("Item", {})

    prefix = f"ISSUE_SOURCE#{issue_date}#"
    q = table.query(
        KeyConditionExpression="pk = :pk AND begins_with(sk, :pref)",
        ExpressionAttributeValues={":pk": pk, ":pref": prefix},
    )
    rows = q.get("Items", [])
    while q.get("LastEvaluatedKey"):
        q = table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :pref)",
            ExpressionAttributeValues={":pk": pk, ":pref": prefix},
            ExclusiveStartKey=q["LastEvaluatedKey"],
        )
        rows.extend(q.get("Items", []))

    messages = _build_messages(issue_date, issue, rows, user_id)

    publish_status = "error"
    try:
        if bot_token and bot_channel:
            try:
                publish_status = _publish_via_bot(
                    bot_token, bot_channel, issue_date, messages, user_id
                )
            except RuntimeError as exc:
                print(f"discord bot publish failed, falling back to webhook: {exc!r}")
                if webhook_url:
                    publish_status = _publish_via_webhook(webhook_url, messages)
                else:
                    raise
        else:
            publish_status = _publish_via_webhook(webhook_url, messages)
    except (urllib.error.URLError, urllib.error.HTTPError, RuntimeError) as exc:
        print(f"discord publish failed: {exc!r}")
        publish_status = "error"

    return {
        "status": event.get("status", "COMPOSED"),
        "issue_date": issue_date,
        "user_id": user_id,
        "issue_sk": issue_sk,
        "discord_publish_status": publish_status,
    }
