"""
lambdas/publish_discord/handler.py

Lambda 4 of 4 in the Market Pulse Step Function.

Reads the composed newsletter from DynamoDB and posts it to Discord
via webhook. Handles Discord's 2000-character message limit by splitting
the newsletter into multiple embeds.
"""
import os
import json
import requests
from datetime import datetime, timezone

try:
    from shared.config import (
        SK_NEWSLETTER, SK_SIGNALS,
        DISCORD_MAX_CHARS,
        DISCORD_EMBED_COLOR_GREEN, DISCORD_EMBED_COLOR_RED,
        DISCORD_EMBED_COLOR_YELLOW, DISCORD_EMBED_COLOR_BLUE,
    )
    from shared.dynamo import load_report
except ImportError:
    import sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
    from shared.config import (
        SK_NEWSLETTER, SK_SIGNALS,
        DISCORD_MAX_CHARS,
        DISCORD_EMBED_COLOR_GREEN, DISCORD_EMBED_COLOR_RED,
        DISCORD_EMBED_COLOR_YELLOW, DISCORD_EMBED_COLOR_BLUE,
    )
    from shared.dynamo import load_report


def _regime_color(regime: str) -> int:
    """Pick an embed color based on the market regime."""
    if "RISK-ON" in regime:
        return DISCORD_EMBED_COLOR_GREEN
    elif "RISK-OFF" in regime:
        return DISCORD_EMBED_COLOR_RED
    else:
        return DISCORD_EMBED_COLOR_YELLOW


def _split_into_chunks(text: str, max_len: int = 4000) -> list[str]:
    """
    Split text into chunks that fit in Discord embed descriptions (max 4096 chars).
    Split on paragraph boundaries where possible.
    """
    if len(text) <= max_len:
        return [text]

    chunks = []
    current = ""
    for paragraph in text.split("\n\n"):
        if len(current) + len(paragraph) + 2 > max_len:
            if current:
                chunks.append(current.strip())
            current = paragraph
        else:
            current = current + "\n\n" + paragraph if current else paragraph

    if current:
        chunks.append(current.strip())

    return chunks


def post_to_discord(webhook_url: str, date_str: str, newsletter: dict, signals: dict) -> bool:
    """
    Post the newsletter to Discord as a rich embed.
    Uses multiple embeds if content exceeds Discord limits.
    """
    regime   = newsletter.get("regime", "UNKNOWN")
    color    = _regime_color(regime)
    text     = newsletter.get("newsletter", "No content generated.")
    rotation = newsletter.get("rotation_alerts", 0)
    cluster  = newsletter.get("cluster_alerts", 0)

    # Regime emoji
    regime_emoji = {"RISK-ON": "🟢", "RISK-OFF": "🔴"}.get(regime, "🟡")

    # Build header embed
    header_embed = {
        "title":       f"📊 Market Pulse — {date_str}",
        "description": f"{regime_emoji} **{regime}**\n"
                       f"{'⚡' * min(rotation, 5)} {rotation} Rotation Alert(s) | "
                       f"{'📊' * min(cluster, 3)} {cluster} Cluster Signal(s)",
        "color":       color,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "footer":      {"text": "Market Pulse • Pre-market brief"},
    }

    # Split newsletter body across embeds if needed
    chunks = _split_into_chunks(text, max_len=4000)

    embeds = [header_embed]
    for i, chunk in enumerate(chunks):
        embed = {
            "description": chunk,
            "color":       color,
        }
        if i == 0:
            embed["title"] = "Morning Brief"
        embeds.append(embed)

    # Discord webhook payload — can send up to 10 embeds per message
    # Split into batches of 10 if needed
    batch_size = 10
    for batch_start in range(0, len(embeds), batch_size):
        batch = embeds[batch_start:batch_start + batch_size]
        payload = {"embeds": batch}

        resp = requests.post(
            webhook_url,
            json=payload,
            headers={"Content-Type": "application/json"},
            timeout=15,
        )

        if resp.status_code not in (200, 204):
            print(f"[Discord] Error posting batch: {resp.status_code} — {resp.text[:200]}")
            return False
        else:
            print(f"[Discord] Posted batch of {len(batch)} embeds successfully")

    return True


def handler(event: dict, context) -> dict:
    date_str    = event.get("date")
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL", "")

    print(f"[publish_discord] Running for date: {date_str}")

    if not webhook_url:
        print("[publish_discord] WARNING: No DISCORD_WEBHOOK_URL set — skipping publish")
        return {"date": date_str, "status": "skipped", "reason": "no webhook configured"}

    # Load newsletter + signals from DynamoDB
    newsletter = load_report(date_str, SK_NEWSLETTER)
    signals    = load_report(date_str, SK_SIGNALS)

    if not newsletter:
        raise ValueError(f"No newsletter found in DynamoDB for {date_str}")

    success = post_to_discord(webhook_url, date_str, newsletter, signals or {})

    return {
        "date":    date_str,
        "status":  "published" if success else "failed",
        "regime":  newsletter.get("regime"),
        "words":   newsletter.get("word_count"),
    }
