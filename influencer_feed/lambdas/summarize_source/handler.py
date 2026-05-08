import json
import os
from datetime import date, datetime, timedelta, timezone

import boto3

from openai_client import chat_completion

TABLE_NAME = os.environ["TABLE_NAME"]
OPENAI_SECRET_ARN = (os.environ.get("OPENAI_SECRET_ARN") or "").strip()
OPENAI_CHEAP_MODEL = (os.environ.get("OPENAI_CHEAP_MODEL") or "gpt-4o-mini").strip()
VALID_TICKER_RE = r"^[A-Z]{1,6}$"
TICKER_BLOCKLIST = {"FUTURES", "E-MINI", "EMINI", "NASDAQ", "SP500", "S&P500", "CRYPTO", "STOCKS"}

# OpenAI structured outputs (JSON schema). Fallback without schema is handled in openai_client.
SOURCE_SUMMARY_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "source_summary",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "key_events": {
                    "type": "string",
                    "description": (
                        "Hearings, votes, regulatory deadlines, ETF flows, unlocks, macro prints, "
                        "or other dated catalysts mentioned. Include dates when stated. "
                        "Use empty string if none discussed."
                    ),
                },
                "technical_levels": {
                    "type": "string",
                    "description": (
                        "Key prices, ranges, support/resistance, targets, or tape structure cited. "
                        "Use empty string if not discussed."
                    ),
                },
                "positioning": {
                    "type": "string",
                    "description": (
                        "Concrete stance: what to buy/sell/avoid/add on dips, with reasoning tied to "
                        "the transcript (not generic platitudes). Multiple sentences OK."
                    ),
                },
                "tickers": {
                    "type": "array",
                    "description": "Explicit symbols discussed; empty array if none.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "direction": {
                                "type": "string",
                                "enum": ["bullish", "bearish", "neutral", "unclear"],
                            },
                            "note": {"type": "string"},
                        },
                        "required": ["ticker", "direction", "note"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": ["key_events", "technical_levels", "positioning", "tickers"],
            "additionalProperties": False,
        },
    },
}


def _get_openai_api_key():
    if not OPENAI_SECRET_ARN:
        return None
    sm = boto3.client("secretsmanager")
    raw = (sm.get_secret_value(SecretId=OPENAI_SECRET_ARN)["SecretString"] or "").strip()
    if not raw:
        return None
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            # If someone pasted loosely formatted JSON, treat it as raw key text.
            return raw
        key = (data.get("api_key") or data.get("OPENAI_API_KEY") or "").strip()
        return key or None
    return raw


def _full_transcript_from_s3(fetch_row, max_chars=28000):
    bucket = fetch_row.get("transcript_s3_bucket")
    manifest_key = fetch_row.get("transcript_manifest_s3_key")
    if not bucket or not manifest_key:
        return ""

    s3 = boto3.client("s3")
    manifest_raw = s3.get_object(Bucket=bucket, Key=manifest_key)["Body"].read().decode("utf-8")
    manifest = json.loads(manifest_raw)
    pieces = []
    for video in manifest.get("videos", []):
        key = video.get("s3_key")
        if not key:
            continue
        payload_raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8")
        payload = json.loads(payload_raw)
        text = (payload.get("transcript_text") or "").strip()
        if text:
            pieces.append(" ".join(text.split()))
    combined = " ".join(pieces).strip()
    if not combined:
        return ""
    if len(combined) > max_chars:
        combined = combined[: max_chars - 3].rstrip() + "..."
    return combined


def _format_source_summary_body(key_events: str, technical_levels: str, positioning: str) -> str:
    parts = []
    ke = (key_events or "").strip()
    tl = (technical_levels or "").strip()
    po = (positioning or "").strip()
    if ke:
        parts.append(f"**Catalysts / events:** {ke}")
    if tl:
        parts.append(f"**Levels / TA:** {tl}")
    if po:
        parts.append(f"**Positioning:** {po}")
    return "\n\n".join(parts) if parts else ""


def _llm_source_summary(*, api_key, display_name, prior_summary, transcript_excerpt):
    prior = (prior_summary or "").strip() or "(none — first day or no prior summary.)"
    body = (transcript_excerpt or "").strip()
    if not body:
        return None
    user_prompt = (
        f"Channel: {display_name}\n\n"
        f"Prior day summary for this channel:\n{prior}\n\n"
        f"Today's transcript excerpt (may be truncated):\n{body}\n\n"
        "Produce the structured response only (JSON matching the schema). "
        "Be specific: preserve dates, bill/act names, conditional scenarios (if X then Y), and numeric levels from the transcript. "
        "key_events / technical_levels: use \"\" only when that category truly does not appear. "
        "positioning: actionable thesis tied to the transcript—avoid generic filler (e.g. 'DCA BTC' or 'watch resistance') unless "
        "the speaker centered the video on that with concrete context. "
        "tickers: one row per symbol clearly discussed; note field should carry the reasoning or scenario (can be a short phrase)."
    )
    return chat_completion(
        api_key=api_key,
        model=OPENAI_CHEAP_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You summarize influencer financial content accurately and conservatively. "
                    "Output must match the JSON schema exactly. "
                    "Do not invent tickers, positions, trades, dates, or legislation not supported by the transcript."
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1400,
        temperature=0.35,
        timeout_s=120,
        response_format=SOURCE_SUMMARY_RESPONSE_FORMAT,
    )


def _extract_json_obj(text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        snippet = raw[start : end + 1]
        try:
            return json.loads(snippet)
        except json.JSONDecodeError:
            return {}
    return {}


def _normalize_ticker(value: str) -> str:
    import re

    ticker = (value or "").strip().upper().replace("$", "")
    if not ticker or ticker in TICKER_BLOCKLIST:
        return ""
    if ticker in {"BTCUSD", "XBTUSD"}:
        return "BTC"
    if ticker == "ETHUSD":
        return "ETH"
    if not re.match(VALID_TICKER_RE, ticker):
        return ""
    return ticker


def _ticker_mentioned_in_text(ticker: str, transcript_excerpt: str) -> bool:
    import re

    text = (transcript_excerpt or "").lower()
    if not text:
        return False
    symbol = ticker.lower()
    if symbol in {"btc", "eth"}:
        aliases = {"btc": ["bitcoin", "btc"], "eth": ["ethereum", "eth"]}[symbol]
        return any(a in text for a in aliases)
    return re.search(rf"(?<![a-z0-9]){re.escape(symbol)}(?![a-z0-9])", text) is not None


def handler(event, context):
    issue_date = event["issue_date"]
    user_id = event.get("user_id", "default")
    source_id = event["source_id"]
    fetch_sk = event["fetch_sk"]
    pk = f"USER#{user_id}"

    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    fetch_row = table.get_item(Key={"pk": pk, "sk": fetch_sk}).get("Item")
    if not fetch_row:
        raise ValueError(f"Missing fetch row for {pk} / {fetch_sk}")

    status = fetch_row.get("status", "UNKNOWN")
    display_name = fetch_row.get("display_name", source_id)
    video_count = int(fetch_row.get("video_count", 0))

    prior_summary = ""
    try:
        prev_day = (date.fromisoformat(issue_date) - timedelta(days=1)).isoformat()
        prior_sk = f"ISSUE_SOURCE#{prev_day}#{source_id}"
        prior_item = table.get_item(Key={"pk": pk, "sk": prior_sk}).get("Item")
        if prior_item:
            prior_summary = (prior_item.get("source_summary") or "").strip()
    except ValueError:
        prior_summary = ""

    if status == "NO_NEW":
        source_summary = f"No updates from {display_name}."
        summary_mode = "no_update"
    else:
        excerpt = _full_transcript_from_s3(fetch_row)
        if not excerpt:
            excerpt = " ".join(((fetch_row.get("raw_text") or "").strip()).split())
            if len(excerpt) > 28000:
                excerpt = excerpt[:27997].rstrip() + "..."

        api_key = _get_openai_api_key()
        source_summary = ""
        summary_mode = "unavailable"
        if not excerpt:
            source_summary = "No transcript text available for this source on this day."
            summary_mode = "no_transcript"
        elif not api_key:
            source_summary = (
                "LLM summary skipped: OpenAI API key is not configured in Secrets Manager for this stack."
            )
        else:
            try:
                model_text = _llm_source_summary(
                    api_key=api_key,
                    display_name=display_name,
                    prior_summary=prior_summary,
                    transcript_excerpt=excerpt,
                ).strip()
                parsed = _extract_json_obj(model_text)
                ke = (parsed.get("key_events") or "").strip()
                tl = (parsed.get("technical_levels") or "").strip()
                po = (parsed.get("positioning") or "").strip()
                legacy_advice = (parsed.get("advice") or "").strip()
                tickers = parsed.get("tickers") if isinstance(parsed.get("tickers"), list) else []
                body = _format_source_summary_body(ke, tl, po)
                if not body and legacy_advice:
                    body = legacy_advice
                if body:
                    source_summary = body
                    summary_mode = "openai"
                else:
                    source_summary = (model_text or "").strip()
                    if source_summary:
                        summary_mode = "openai"
                source_tickers = []
                for t in tickers:
                    if not isinstance(t, dict):
                        continue
                    ticker = _normalize_ticker(t.get("ticker") or "")
                    if not ticker:
                        continue
                    if not _ticker_mentioned_in_text(ticker, excerpt):
                        continue
                    source_tickers.append(
                        {
                            "ticker": ticker,
                            "direction": (t.get("direction") or "unclear").strip().lower(),
                            "note": (t.get("note") or "").strip(),
                        }
                    )
            except Exception as exc:
                print(f"summarize_source openai error: {exc!r}")
                source_tickers = []

            if summary_mode != "openai":
                source_summary = (
                    "LLM summary could not be produced (OpenAI error or empty model response). "
                    "Transcripts remain in S3 on the corresponding FETCH row."
                )
                summary_mode = "unavailable"
                source_tickers = []

    if status == "NO_NEW":
        source_tickers = []
    if "source_tickers" not in locals():
        source_tickers = []
    source_links = [m.get("video_url") for m in (fetch_row.get("video_meta") or []) if m.get("video_url")]

    issue_source_sk = f"ISSUE_SOURCE#{issue_date}#{source_id}"
    item = {
        "pk": pk,
        "sk": issue_source_sk,
        "issue_date": issue_date,
        "source_id": source_id,
        "display_name": display_name,
        "status": status,
        "video_count": video_count,
        "source_summary": source_summary,
        "source_tickers": source_tickers,
        "source_links": source_links[:5],
        "source_summary_mode": summary_mode,
        "source_summary_model": OPENAI_CHEAP_MODEL if summary_mode == "openai" else "n/a",
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    table.put_item(Item=item)

    return {
        "source_id": source_id,
        "issue_source_sk": issue_source_sk,
        "status": status,
        "video_count": video_count,
        "display_name": display_name,
    }
