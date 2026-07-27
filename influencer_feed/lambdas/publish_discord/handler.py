import json
import os
import re
import time
import urllib.error
import urllib.request

import boto3

from content_filter import NO_UPDATES_GLOBAL, is_actionable_issue_source
from video_links import links_for_report

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


def _retry_after_seconds(err_body: str, attempt: int) -> float:
    try:
        data = json.loads(err_body)
        ra = data.get("retry_after")
        if ra is not None:
            return float(ra) + 0.15
    except (json.JSONDecodeError, TypeError, ValueError):
        pass
    return min(8.0, 0.5 * (2**attempt))


def _http_json(url: str, *, method: str, headers: dict, body: dict | None = None, max_attempts: int = 5) -> dict:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    last_exc: BaseException | None = None
    for attempt in range(max_attempts):
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=25) as resp:
                raw = resp.read().decode("utf-8", errors="replace").strip()
                if not raw:
                    return {}
                try:
                    return json.loads(raw)
                except json.JSONDecodeError:
                    return {}
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")[:2000]
            last_exc = RuntimeError(f"Discord HTTP {exc.code} {method} {url}: {err_body}")
            if exc.code == 429 and attempt < max_attempts - 1:
                time.sleep(_retry_after_seconds(err_body, attempt))
                continue
            raise last_exc from exc
        except urllib.error.URLError as exc:
            last_exc = exc
            if attempt < max_attempts - 1:
                time.sleep(0.5 * (2**attempt))
                continue
            raise
    if last_exc is not None:
        raise last_exc
    return {}


def _bot_post_message(
    token: str,
    channel_id: str,
    content: str,
    *,
    message_reference: dict | None = None,
) -> dict:
    url = f"{DISCORD_API}/channels/{channel_id}/messages"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bot {token}",
        "User-Agent": USER_AGENT,
    }
    payload: dict = {"content": content}
    if message_reference:
        payload["message_reference"] = message_reference
    return _http_json(url, method="POST", headers=headers, body=payload)


def _bot_create_thread(token: str, channel_id: str, message_id: str, name: str) -> dict:
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bot {token}",
        "User-Agent": USER_AGENT,
    }
    body = {"name": name[:90] or "thread", "auto_archive_duration": 1440, "type": 11}
    # Prefer message-attached public thread (needs CREATE_PUBLIC_THREADS on parent channel).
    url = f"{DISCORD_API}/channels/{channel_id}/messages/{message_id}/threads"
    try:
        return _http_json(url, method="POST", headers=headers, body=body)
    except RuntimeError as exc:
        print(f"discord thread from message failed: {exc!r}")
    # Fallback: start thread without message (still links to channel).
    url2 = f"{DISCORD_API}/channels/{channel_id}/threads"
    body2 = {**body, "message_id": message_id}
    return _http_json(url2, method="POST", headers=headers, body=body2)


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


def _format_summary_for_discord(summary: str) -> str:
    """Render stored summary text; unwrap JSON blobs when summarize stored raw schema output."""
    text = (summary or "").strip()
    if not text:
        return ""
    if text.startswith("{"):
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return text
        if isinstance(data, dict):
            parts = []
            ke = (data.get("key_events") or "").strip()
            tl = (data.get("technical_levels") or "").strip()
            po = (data.get("positioning") or data.get("advice") or "").strip()
            if ke:
                parts.append(f"**Catalysts / events:** {ke}")
            if tl:
                parts.append(f"**Levels / TA:** {tl}")
            if po:
                parts.append(f"**Positioning:** {po}")
            if parts:
                return "\n\n".join(parts)
            tickers = data.get("tickers")
            if isinstance(tickers, list) and tickers:
                lines = []
                for t in tickers[:8]:
                    if not isinstance(t, dict):
                        continue
                    sym = (t.get("ticker") or "").strip().upper()
                    if sym:
                        lines.append(f"- **{sym}** ({t.get('direction') or 'unclear'})")
                if lines:
                    return "**Tickers mentioned:**\n" + "\n".join(lines)
    return text


def _source_thread_message(
    row: dict,
    *,
    issue_date: str,
    table,
    pk: str,
) -> str | None:
    source_id = row.get("source_id") or ""
    fetch_row = (
        table.get_item(Key={"pk": pk, "sk": f"FETCH#{issue_date}#{source_id}"}).get("Item") or {}
        if source_id
        else {}
    )
    if not is_actionable_issue_source(row, fetch_row=fetch_row):
        return None
    name = row.get("display_name") or source_id or "unknown"
    summary = _format_summary_for_discord(row.get("source_summary") or "")
    if not summary or summary.startswith("{"):
        summary = "(Summary text unavailable — see video link.)"
    tickers = row.get("source_tickers") or []
    ticker_text = ", ".join(
        [f"{t.get('ticker','').upper()}({(t.get('direction') or 'unclear')})" for t in tickers if isinstance(t, dict)]
    )
    links = links_for_report(fetch_row=fetch_row) if fetch_row else []
    if not links:
        links = [
            u
            for u in (row.get("source_links") or [])
            if isinstance(u, str) and u.startswith("http")
        ]
    src_lines = [f"**{name}**", summary]
    if ticker_text:
        src_lines.append(f"Tickers: {ticker_text}")
    for url in links[:3]:
        src_lines.append(f"Video: {url}")
    return "\n".join(src_lines).strip()


def _build_posts(
    issue_date: str,
    issue: dict,
    source_rows: list[dict],
    user_id: str,
    *,
    table,
    pk: str,
) -> tuple[list[str], list[str]]:
    """Return (main_channel_chunks, per_source_thread_messages)."""
    title = "Crypto Digest" if user_id == "crypto" else "Macro Digest"
    lines = [f"**{title} — {issue_date}**", ""]
    top = (issue.get("global_summary_smol", "") or "").strip()
    if not top or top.lower().startswith("no source rows found"):
        top = NO_UPDATES_GLOBAL
    if (issue.get("global_summary_mode") or "") == "none" or top == NO_UPDATES_GLOBAL:
        top = NO_UPDATES_GLOBAL
    lines.append(top)
    is_quiet = top == NO_UPDATES_GLOBAL or (issue.get("global_summary_mode") or "") == "none"
    catalysts = (issue.get("global_catalysts") or "").strip()
    if catalysts and not is_quiet:
        lines.extend(["", "**Catalysts / dates to watch**", catalysts])
    technical = (issue.get("global_technical") or "").strip()
    if technical and not is_quiet:
        lines.extend(["", "**Levels / TA snapshot**", technical])
    shift = (issue.get("global_summary_shift") or "").strip()
    if shift and not is_quiet:
        lines.extend(["", f"**What changed:** {shift}"])
    ticker_focus = issue.get("global_ticker_focus") or []
    if ticker_focus and not is_quiet:
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

    thread_messages: list[str] = []
    source_names: list[str] = []
    for row in sorted(source_rows, key=lambda r: (r.get("display_name") or r.get("source_id") or "")):
        block = _source_thread_message(row, issue_date=issue_date, table=table, pk=pk)
        if not block:
            continue
        name = row.get("display_name") or row.get("source_id") or "unknown"
        source_names.append(name)
        for chunk in _discord_content_chunks(block):
            thread_messages.append(chunk)

    if source_names and not is_quiet:
        lines.extend(["", f"**Influencers in thread ({len(source_names)}):**"])
        for name in source_names:
            lines.append(f"- {name}")
        lines.append("_Per-channel notes are in the thread below._")

    main_chunks = _discord_content_chunks("\n".join(lines))
    return main_chunks, thread_messages


def _post_bot_messages(
    token: str,
    target_channel: str,
    messages: list[str],
    *,
    parent_channel_id: str = "",
    root_message_id: str = "",
    use_replies: bool = False,
    post_delay_s: float = 0.55,
) -> tuple[int, int]:
    """Post messages with pacing + 429 retry. Returns (ok_count, fail_count)."""
    ok = 0
    fail = 0
    for i, msg in enumerate(messages):
        if i > 0 and post_delay_s > 0:
            time.sleep(post_delay_s)
        ref = None
        if use_replies and root_message_id and parent_channel_id:
            ref = {
                "message_id": root_message_id,
                "channel_id": parent_channel_id,
                "fail_if_not_exists": False,
            }
        try:
            _bot_post_message(token, target_channel, msg, message_reference=ref)
            ok += 1
        except RuntimeError as exc:
            fail += 1
            print(f"discord bot post failed ch={target_channel}: {exc!r}")
    return ok, fail


def _publish_via_bot(
    token: str,
    channel_id: str,
    issue_date: str,
    main_chunks: list[str],
    thread_messages: list[str],
    user_id: str,
) -> str:
    if not main_chunks:
        raise RuntimeError("No main digest content to publish")

    print(
        f"discord publish: main_chunks={len(main_chunks)} thread_messages={len(thread_messages)} "
        f"channel={channel_id}"
    )

    root = _bot_post_message(token, channel_id, main_chunks[0])
    root_id = (root.get("id") or "").strip() if isinstance(root, dict) else ""
    if not root_id:
        raise RuntimeError("Discord bot post returned no message id")

    thread_name = f"Crypto source notes — {issue_date}" if user_id == "crypto" else f"Source notes — {issue_date}"
    thread_id = ""
    if thread_messages:
        try:
            thread = _bot_create_thread(token, channel_id, root_id, thread_name)
            thread_id = (thread.get("id") or "").strip() if isinstance(thread, dict) else ""
            print(f"discord thread create response id={thread_id!r} keys={list(thread.keys()) if isinstance(thread, dict) else []}")
        except RuntimeError as exc:
            print(f"discord bot thread create failed: {exc!r}")

    use_replies = not thread_id
    if use_replies and thread_messages:
        print("discord: falling back to message replies on root post (no thread channel id)")

    target_channel = thread_id or channel_id
    extra_ok, extra_fail = _post_bot_messages(
        token,
        target_channel,
        main_chunks[1:],
        parent_channel_id=channel_id,
        root_message_id=root_id,
        use_replies=False,
    )
    src_ok, src_fail = _post_bot_messages(
        token,
        target_channel,
        thread_messages,
        parent_channel_id=channel_id,
        root_message_id=root_id,
        use_replies=use_replies,
    )
    print(
        f"discord publish done: thread_id={thread_id or '(none)'} "
        f"extra_ok={extra_ok} src_ok={src_ok} src_fail={src_fail}"
    )

    if thread_messages and src_ok == 0:
        print(
            f"discord WARNING: 0/{len(thread_messages)} source messages posted "
            f"(thread_id={thread_id or 'none'} fail={src_fail})"
        )

    if thread_id and src_ok > 0:
        return "posted_thread"
    if use_replies and src_ok > 0:
        return "posted_replies"
    return "posted_channel"


def _publish_via_webhook(webhook_url: str, main_chunks: list[str], thread_messages: list[str]) -> str:
    root = _post_webhook(webhook_url, main_chunks[0])
    root_id = (root.get("id") or "").strip() if isinstance(root, dict) else ""
    channel_id = (root.get("channel_id") or "").strip() if isinstance(root, dict) else ""
    tail = list(main_chunks[1:]) + list(thread_messages)
    for i, msg in enumerate(tail):
        if i > 0:
            time.sleep(0.55)
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
    print(f"discord publish: loaded {len(rows)} ISSUE_SOURCE row(s) for {issue_date}")
    while q.get("LastEvaluatedKey"):
        q = table.query(
            KeyConditionExpression="pk = :pk AND begins_with(sk, :pref)",
            ExpressionAttributeValues={":pk": pk, ":pref": prefix},
            ExclusiveStartKey=q["LastEvaluatedKey"],
        )
        rows.extend(q.get("Items", []))

    main_chunks, thread_messages = _build_posts(
        issue_date, issue, rows, user_id, table=table, pk=pk
    )
    if not main_chunks:
        return {
            "status": event.get("status", "COMPOSED"),
            "issue_date": issue_date,
            "user_id": user_id,
            "issue_sk": issue_sk,
            "discord_publish_status": "skipped_no_content",
        }

    publish_status = "error"
    try:
        if bot_token and bot_channel:
            try:
                publish_status = _publish_via_bot(
                    bot_token,
                    bot_channel,
                    issue_date,
                    main_chunks,
                    thread_messages,
                    user_id,
                )
            except RuntimeError as exc:
                print(f"discord bot publish failed, falling back to webhook: {exc!r}")
                if webhook_url:
                    publish_status = _publish_via_webhook(webhook_url, main_chunks, thread_messages)
                else:
                    raise
        else:
            publish_status = _publish_via_webhook(webhook_url, main_chunks, thread_messages)
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
