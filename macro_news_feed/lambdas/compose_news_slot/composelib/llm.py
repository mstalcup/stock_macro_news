from __future__ import annotations

import json
import re

from openai_client import chat_completion

VALID_TICKER_RE = re.compile(r"^[A-Z]{1,6}$")
TICKER_BLOCKLIST = frozenset(
    {"FUTURES", "NASDAQ", "SP500", "S&P500", "CRYPTO", "STOCKS", "MARKET", "NEWS"}
)

ARTICLE_NOTES_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "article_notes",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "notes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "article_id": {"type": "string"},
                            "macro_theme": {"type": "string"},
                            "market_impact": {
                                "type": "string",
                                "description": "One sentence: who wins/loses, direction if stated.",
                            },
                            "catalysts": {
                                "type": "string",
                                "description": "Dates/events mentioned; empty if none.",
                            },
                        },
                        "required": ["article_id", "macro_theme", "market_impact", "catalysts"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["notes"],
            "additionalProperties": False,
        },
    },
}

MACRO_DIGEST_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "macro_digest",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "market_bias": {
                    "type": "string",
                    "enum": ["risk_on", "risk_off", "mixed", "unclear"],
                },
                "executive_summary": {
                    "type": "string",
                    "description": "Two short paragraphs (~160-220 words): what is driving tape today.",
                },
                "dominant_themes": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "theme": {"type": "string"},
                            "sentiment": {
                                "type": "string",
                                "enum": ["bullish", "bearish", "mixed", "neutral"],
                            },
                            "why_it_matters": {"type": "string"},
                            "headline_refs": {
                                "type": "array",
                                "items": {"type": "string"},
                            },
                        },
                        "required": ["theme", "sentiment", "why_it_matters", "headline_refs"],
                        "additionalProperties": False,
                    },
                },
                "catalysts_ahead": {
                    "type": "string",
                    "description": "Bullet lines: dated events, data, policy, geopolitical milestones.",
                },
                "asset_class_notes": {
                    "type": "object",
                    "properties": {
                        "equities": {"type": "string"},
                        "rates_fx": {"type": "string"},
                        "commodities": {"type": "string"},
                        "crypto": {"type": "string"},
                    },
                    "required": ["equities", "rates_fx", "commodities", "crypto"],
                    "additionalProperties": False,
                },
                "ticker_watchlist": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "ticker": {"type": "string"},
                            "bias": {
                                "type": "string",
                                "enum": ["bullish", "bearish", "mixed", "neutral"],
                            },
                            "note": {"type": "string"},
                        },
                        "required": ["ticker", "bias", "note"],
                        "additionalProperties": False,
                    },
                },
                "risks_to_watch": {"type": "string"},
                "vs_prior_slot": {
                    "type": "string",
                    "description": "One sentence shift vs prior digest; empty if no prior.",
                },
                "actionable_takeaways": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "3-6 concrete trader actions (hedges, sectors, wait-for-data).",
                },
            },
            "required": [
                "market_bias",
                "executive_summary",
                "dominant_themes",
                "catalysts_ahead",
                "asset_class_notes",
                "ticker_watchlist",
                "risks_to_watch",
                "vs_prior_slot",
                "actionable_takeaways",
            ],
            "additionalProperties": False,
        },
    },
}


def extract_json_obj(text: str) -> dict:
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


def normalize_ticker(value: str) -> str:
    ticker = (value or "").strip().upper().replace("$", "")
    if ticker in {"BTCUSD", "XBTUSD"}:
        return "BTC"
    if ticker == "ETHUSD":
        return "ETH"
    if not ticker or ticker in TICKER_BLOCKLIST:
        return ""
    if not VALID_TICKER_RE.match(ticker):
        return ""
    return ticker


def build_headline_blocks(articles: list[dict], *, max_articles: int = 28) -> list[str]:
    blocks = []
    for art in articles[:max_articles]:
        aid = art.get("article_id", "")
        title = (art.get("title") or "").strip()
        summary = (art.get("summary") or "").strip()
        if len(summary) > 420:
            summary = summary[:417].rstrip() + "..."
        prov = ",".join(art.get("providers") or [])
        tickers = ",".join(art.get("tickers") or [])[:80]
        blocks.append(
            f"id={aid}\nproviders={prov}\ntickers={tickers or 'n/a'}\n"
            f"title={title}\nsummary={summary or '(no summary)'}"
        )
    return blocks


def llm_article_notes(
    *,
    api_key: str,
    model: str,
    issue_date: str,
    slot: str,
    blocks: list[str],
) -> list[dict]:
    if not blocks:
        return []
    user = (
        f"Issue date: {issue_date}\nSlot: {slot}\n\n"
        "For each headline block, extract macro trading relevance only from the text provided.\n"
        "Do not invent tickers, prices, or dates.\n\n"
        + "\n\n---\n\n".join(blocks)
    )
    text = chat_completion(
        api_key=api_key,
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a macro trading desk analyst. Output JSON matching the schema exactly."
                ),
            },
            {"role": "user", "content": user},
        ],
        max_tokens=1400,
        temperature=0.25,
        timeout_s=90,
        response_format=ARTICLE_NOTES_RESPONSE_FORMAT,
    )
    parsed = extract_json_obj(text)
    notes = parsed.get("notes")
    return notes if isinstance(notes, list) else []


def llm_macro_digest(
    *,
    api_key: str,
    model: str,
    issue_date: str,
    slot: str,
    slot_label: str,
    prior_text: str,
    headline_blocks: list[str],
    article_notes: list[dict],
) -> dict:
    prior = (prior_text or "").strip() or "(none — first digest for this comparison window.)"
    notes_blob = json.dumps(article_notes, ensure_ascii=False)[:12000]
    headlines = "\n\n".join(headline_blocks[:28])
    user = (
        f"Issue date: {issue_date}\n"
        f"Slot: {slot} ({slot_label})\n\n"
        f"Prior digest (same feed, earlier slot or prior day):\n{prior}\n\n"
        f"Per-article notes JSON:\n{notes_blob}\n\n"
        f"Headlines:\n{headlines}\n\n"
        "Produce the structured macro digest for an active trader. "
        + (
            "This is the AFTERNOON recap slot: focus on what changed since the morning briefing; "
            "de-emphasize stories already covered unless there is a material update. "
            if slot == "pre_close"
            else ""
        )
        + "Prioritize cross-headline themes (geopolitics, Fed, oil, mega-cap, crypto). "
        "actionable_takeaways must be specific (e.g. sector hedges, wait for CPI), not generic advice. "
        "ticker_watchlist: valid US symbols/ETFs/crypto tickers only, max 12."
    )
    text = chat_completion(
        api_key=api_key,
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You synthesize macro headlines into an actionable trading digest. "
                    "Stay faithful to inputs; do not fabricate data releases or price levels."
                ),
            },
            {"role": "user", "content": user},
        ],
        max_tokens=1800,
        temperature=0.35,
        timeout_s=120,
        response_format=MACRO_DIGEST_RESPONSE_FORMAT,
    )
    parsed = extract_json_obj(text)
    return normalize_digest(parsed)


def normalize_digest(parsed: dict) -> dict:
    bias = (parsed.get("market_bias") or "mixed").strip().lower()
    if bias not in ("risk_on", "risk_off", "mixed", "unclear"):
        bias = "mixed"

    themes = []
    for t in parsed.get("dominant_themes") or []:
        if not isinstance(t, dict):
            continue
        refs = t.get("headline_refs")
        if not isinstance(refs, list):
            refs = []
        themes.append(
            {
                "theme": (t.get("theme") or "").strip(),
                "sentiment": (t.get("sentiment") or "mixed").strip().lower(),
                "why_it_matters": (t.get("why_it_matters") or "").strip(),
                "headline_refs": [str(x).strip() for x in refs if str(x).strip()][:5],
            }
        )

    assets = parsed.get("asset_class_notes") or {}
    if not isinstance(assets, dict):
        assets = {}
    asset_notes = {
        "equities": (assets.get("equities") or "").strip(),
        "rates_fx": (assets.get("rates_fx") or "").strip(),
        "commodities": (assets.get("commodities") or "").strip(),
        "crypto": (assets.get("crypto") or "").strip(),
    }

    watch = []
    for t in parsed.get("ticker_watchlist") or []:
        if not isinstance(t, dict):
            continue
        sym = normalize_ticker(t.get("ticker") or "")
        if not sym:
            continue
        watch.append(
            {
                "ticker": sym,
                "bias": (t.get("bias") or "mixed").strip().lower(),
                "note": (t.get("note") or "").strip(),
            }
        )

    takeaways = []
    for item in parsed.get("actionable_takeaways") or []:
        if isinstance(item, str) and item.strip():
            takeaways.append(item.strip())

    return {
        "market_bias": bias,
        "executive_summary": (parsed.get("executive_summary") or "").strip(),
        "dominant_themes": themes,
        "catalysts_ahead": (parsed.get("catalysts_ahead") or "").strip(),
        "asset_class_notes": asset_notes,
        "ticker_watchlist": watch[:12],
        "risks_to_watch": (parsed.get("risks_to_watch") or "").strip(),
        "vs_prior_slot": (parsed.get("vs_prior_slot") or "").strip(),
        "actionable_takeaways": takeaways[:6],
    }
