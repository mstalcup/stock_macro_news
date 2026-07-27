import json
import os
from datetime import date, timedelta

import boto3

from openai_client import chat_completion

TABLE_NAME = os.environ["TABLE_NAME"]
OPENAI_SECRET_ARN = (os.environ.get("OPENAI_SECRET_ARN") or "").strip()
OPENAI_SMART_MODEL = (os.environ.get("OPENAI_SMART_MODEL") or "gpt-4o").strip()
VALID_TICKER_RE = r"^[A-Z]{1,6}$"
TICKER_BLOCKLIST = {"FUTURES", "E-MINI", "EMINI", "NASDAQ", "SP500", "S&P500", "CRYPTO", "STOCKS"}

GLOBAL_SUMMARY_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "global_digest",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "overall_advice": {
                    "type": "string",
                    "description": (
                        "Two short paragraphs (~180-240 words total): synthesize cross-channel views into actionable guidance. "
                        "Preserve concrete inputs (bill/act names, committee dates, conditional scenarios if/then, ranges); when sources disagree, say so. "
                        "Avoid generic platitudes unless that truly is the only overlapping message."
                    ),
                },
                "catalysts_watch": {
                    "type": "string",
                    "description": (
                        "Bullet-style lines OK. Merge hearings, votes, deadlines, regulatory milestones from inputs; "
                        "empty string if none mentioned."
                    ),
                },
                "technical_snapshot": {
                    "type": "string",
                    "description": (
                        "Merge cited levels, ranges, and tape bias across symbols; empty string if sparse TA in inputs."
                    ),
                },
                "day_over_day_shift": {
                    "type": "string",
                    "description": "One sentence how today's synthesis differs from yesterday's digest.",
                },
                "ticker_focus": {
                    "type": "array",
                    "description": "Cross-channel consensus on specific symbols only.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "consensus": {
                                "type": "string",
                                "enum": ["bullish", "bearish", "mixed", "neutral"],
                            },
                            "note": {"type": "string"},
                        },
                        "required": ["ticker", "consensus", "note"],
                        "additionalProperties": False,
                    },
                },
            },
            "required": [
                "overall_advice",
                "catalysts_watch",
                "technical_snapshot",
                "day_over_day_shift",
                "ticker_focus",
            ],
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


def _heuristic_global(fetched_rows, issue_date):
    top_lines = []
    for row in sorted(fetched_rows, key=lambda r: r.get("display_name", r.get("source_id", ""))):
        summary = (row.get("source_summary", "") or "").strip()
        if len(summary) > 480:
            summary = summary[:477].rstrip() + "..."
        top_lines.append(f"{row.get('display_name', row.get('source_id'))}: {summary}")
    return " ".join(top_lines)


MAX_CHARS_PER_SOURCE_BLOCK = 4500


def _llm_global(*, api_key, issue_date, prior_digest, blocks):
    prior = (prior_digest or "").strip() or "(none — no prior digest.)"
    channels = "\n\n".join(blocks)
    user_prompt = (
        f"Issue date: {issue_date}\n\n"
        f"Yesterday's digest paragraph:\n{prior}\n\n"
        f"Today's per-channel summaries:\n{channels}\n\n"
        "Produce the structured response only (JSON matching the schema). "
        "overall_advice: rich synthesis—not a shallow recap. Carry forward legislative timelines, dates, and "
        "conditional trades when channels supplied them. "
        "catalysts_watch / technical_snapshot: pull specifics from inputs; use \"\" if truly absent. "
        "ticker_focus: valid symbols only (e.g. NVDA, SPY, BTC, ETH, COIN), max ~12 rows; notes should reflect disagreement."
    )
    return chat_completion(
        api_key=api_key,
        model=OPENAI_SMART_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "You merge influencer summaries into one digest. Output must match the JSON schema exactly. "
                    "Stay faithful to inputs; do not invent tickers, dates, or legislation. "
                    "Prefer specificity over generic portfolio slogans."
                ),
            },
            {"role": "user", "content": user_prompt},
        ],
        max_tokens=1600,
        temperature=0.35,
        timeout_s=120,
        response_format=GLOBAL_SUMMARY_RESPONSE_FORMAT,
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
        try:
            return json.loads(raw[start : end + 1])
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


def handler(event, context):
    issue_date = event["issue_date"]
    user_id = event.get("user_id", "default")
    pk = f"USER#{user_id}"
    prefix = f"ISSUE_SOURCE#{issue_date}#"

    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    query_kwargs = {
        "KeyConditionExpression": "pk = :pk AND begins_with(sk, :pref)",
        "ExpressionAttributeValues": {":pk": pk, ":pref": prefix},
    }
    rows = []
    while True:
        resp = table.query(**query_kwargs)
        rows.extend(resp.get("Items", []))
        last_key = resp.get("LastEvaluatedKey")
        if not last_key:
            break
        query_kwargs["ExclusiveStartKey"] = last_key

    fetched_rows = [r for r in rows if r.get("status") == "FETCHED"]
    no_new_rows = [r for r in rows if r.get("status") == "NO_NEW"]

    def _actionable(row: dict) -> bool:
        mode = (row.get("source_summary_mode") or "").strip().lower()
        if mode != "openai":
            return False
        summary = (row.get("source_summary") or "").strip()
        if not summary or summary.lower().startswith("no transcript"):
            return False
        return True

    actionable_rows = [r for r in fetched_rows if _actionable(r)]

    # Drop stale per-source rows (e.g. old "no transcript" placeholders).
    actionable_ids = {r.get("source_id") for r in actionable_rows}
    for row in rows:
        sid = row.get("source_id")
        if not sid or sid in actionable_ids:
            continue
        table.delete_item(Key={"pk": pk, "sk": row["sk"]})

    prior_digest = ""
    try:
        prev_day = (date.fromisoformat(issue_date) - timedelta(days=1)).isoformat()
        prior_issue = table.get_item(Key={"pk": pk, "sk": f"ISSUE#{prev_day}"}).get("Item")
        if prior_issue:
            prior_digest = (prior_issue.get("global_summary_smol") or "").strip()
    except ValueError:
        prior_digest = ""

    global_mode = "none"
    if actionable_rows:
        blocks = []
        for row in sorted(actionable_rows, key=lambda r: r.get("display_name", r.get("source_id", ""))):
            name = row.get("display_name") or row.get("source_id", "")
            summary = (row.get("source_summary", "") or "").strip()
            tickers = row.get("source_tickers") or []
            if len(summary) > MAX_CHARS_PER_SOURCE_BLOCK:
                summary = summary[: MAX_CHARS_PER_SOURCE_BLOCK - 3].rstrip() + "..."
            ticker_text = ", ".join(
                [f"{t.get('ticker')}({t.get('direction', 'unclear')})" for t in tickers if isinstance(t, dict)]
            )
            if len(ticker_text) > 400:
                ticker_text = ticker_text[:397].rstrip() + "..."
            blocks.append(f"{name}: channel_summary=\n{summary}\ntickers={ticker_text or 'none'}")

        api_key = _get_openai_api_key()
        global_summary = ""
        global_shift = ""
        global_catalysts = ""
        global_technical = ""
        global_ticker_focus = []
        if api_key and blocks:
            try:
                model_text = _llm_global(
                    api_key=api_key,
                    issue_date=issue_date,
                    prior_digest=prior_digest,
                    blocks=blocks,
                ).strip()
                parsed = _extract_json_obj(model_text)
                global_summary = (parsed.get("overall_advice") or "").strip()
                global_shift = (parsed.get("day_over_day_shift") or "").strip()
                global_catalysts = (parsed.get("catalysts_watch") or "").strip()
                global_technical = (parsed.get("technical_snapshot") or "").strip()
                ticker_focus = parsed.get("ticker_focus")
                if isinstance(ticker_focus, list):
                    for t in ticker_focus:
                        if not isinstance(t, dict):
                            continue
                        ticker = _normalize_ticker(t.get("ticker") or "")
                        if not ticker:
                            continue
                        global_ticker_focus.append(
                            {
                                "ticker": ticker,
                                "consensus": (t.get("consensus") or "mixed").strip().lower(),
                                "note": (t.get("note") or "").strip(),
                            }
                        )
                if global_summary:
                    global_mode = "openai"
                else:
                    global_summary = model_text
            except Exception as exc:
                print(f"build_issue_summary openai error: {exc!r}")
                global_summary = ""

        if not global_summary:
            global_summary = _heuristic_global(actionable_rows, issue_date)
            global_ticker_focus = []
            global_shift = ""
            global_catalysts = ""
            global_technical = ""
            global_mode = "heuristic"
    else:
        global_summary = "No updates today."
        global_shift = ""
        global_catalysts = ""
        global_technical = ""
        global_ticker_focus = []
        global_mode = "none"

    return {
        "issue_date": issue_date,
        "user_id": user_id,
        "global_summary_smol": global_summary,
        "global_summary_shift": global_shift,
        "global_catalysts": global_catalysts,
        "global_technical": global_technical,
        "global_ticker_focus": global_ticker_focus,
        "global_summary_mode": global_mode,
        "global_summary_model": OPENAI_SMART_MODEL if global_mode == "openai" else "n/a",
        "fetched_sources": len(actionable_rows),
        "no_new_sources": len(no_new_rows),
        "source_count": len(rows),
    }
