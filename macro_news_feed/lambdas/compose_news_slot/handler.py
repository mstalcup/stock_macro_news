"""
Compose macro trading digest from deduped headlines (NewsAPI + Finnhub).

Reads deduped.json from S3, optional LLM article notes + global digest, writes digest.json.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone

from composelib.format import format_digest_markdown
from composelib.heuristic import build_heuristic_digest
from composelib.llm import build_headline_blocks, llm_article_notes, llm_macro_digest, normalize_digest
from composelib.s3_io import load_deduped, load_prior_digest_text, write_digest_artifacts
from composelib.secrets import load_openai_api_key

BUCKET = os.environ["NEWS_ARTIFACTS_BUCKET"]
OPENAI_CHEAP_MODEL = (os.environ.get("OPENAI_CHEAP_MODEL") or "gpt-4o-mini").strip()
OPENAI_SMART_MODEL = (os.environ.get("OPENAI_SMART_MODEL") or "gpt-4o").strip()
SLOT_LABELS = {
    "pre_open": "Pre-market open (6:25 AM PT)",
    "pre_close": "Pre-market close (12:50 PM PT)",
}
VALID_SLOTS = frozenset({"pre_open", "pre_close"})


def handler(event, context):
    event = event or {}
    issue_date = (event.get("issue_date") or "").strip()
    slot = (event.get("slot") or "").strip()
    if slot not in VALID_SLOTS:
        raise ValueError(f"slot must be one of {sorted(VALID_SLOTS)}, got {slot!r}")

    deduped_s3_key = (event.get("deduped_s3_key") or "").strip()
    fetch_status = (event.get("fetch_status") or "").strip()

    deduped_doc = load_deduped(
        bucket=BUCKET,
        issue_date=issue_date,
        slot=slot,
        deduped_s3_key=deduped_s3_key or None,
    )
    if not issue_date:
        issue_date = deduped_doc.get("issue_date", "")
    if not issue_date:
        raise ValueError("issue_date required")

    articles = deduped_doc.get("articles") or []
    if not deduped_s3_key:
        deduped_s3_key = f"v1/date={issue_date}/slot={slot}/deduped.json"

    prior_text, prior_key = load_prior_digest_text(
        bucket=BUCKET, issue_date=issue_date, slot=slot
    )
    slot_label = SLOT_LABELS.get(slot, slot)
    blocks = build_headline_blocks(articles)

    compose_mode = "heuristic"
    compose_models: dict[str, str] = {}
    article_notes: list[dict] = []
    digest_body: dict

    api_key = load_openai_api_key()
    if api_key and blocks:
        try:
            article_notes = llm_article_notes(
                api_key=api_key,
                model=OPENAI_CHEAP_MODEL,
                issue_date=issue_date,
                slot=slot,
                blocks=blocks,
            )
            compose_models["article_notes"] = OPENAI_CHEAP_MODEL
            raw_digest = llm_macro_digest(
                api_key=api_key,
                model=OPENAI_SMART_MODEL,
                issue_date=issue_date,
                slot=slot,
                slot_label=slot_label,
                prior_text=prior_text,
                headline_blocks=blocks,
                article_notes=article_notes,
            )
            if raw_digest.get("executive_summary"):
                digest_body = raw_digest
                compose_mode = "openai"
                compose_models["digest"] = OPENAI_SMART_MODEL
            else:
                digest_body = build_heuristic_digest(
                    articles=articles,
                    issue_date=issue_date,
                    slot=slot,
                    prior_text=prior_text,
                )
        except Exception as exc:
            print(f"compose_news_slot openai error: {exc!r}")
            digest_body = build_heuristic_digest(
                articles=articles,
                issue_date=issue_date,
                slot=slot,
                prior_text=prior_text,
            )
            compose_mode = "heuristic_fallback"
    else:
        digest_body = build_heuristic_digest(
            articles=articles,
            issue_date=issue_date,
            slot=slot,
            prior_text=prior_text,
        )
        if not api_key:
            print("compose_news_slot: no OpenAI key — heuristic digest")

    # If OpenAI returned empty themes but we have articles, normalize still applied
    if compose_mode.startswith("openai") and not digest_body.get("dominant_themes") and articles:
        digest_body = normalize_digest(digest_body)

    composed_at = datetime.now(timezone.utc).isoformat()
    markdown = format_digest_markdown(
        issue_date=issue_date,
        slot=slot,
        slot_label=slot_label,
        digest=digest_body,
    )

    s3_out = write_digest_artifacts(
        bucket=BUCKET,
        issue_date=issue_date,
        slot=slot,
        deduped_s3_key=deduped_s3_key,
        deduped_count=len(articles),
        digest_body=digest_body,
        compose_meta={
            "composed_at": composed_at,
            "compose_mode": compose_mode,
            "compose_models": compose_models,
            "prior_digest_s3_key": prior_key,
            "article_notes": article_notes,
            "digest_markdown": markdown,
        },
    )

    status = "ok" if digest_body.get("executive_summary") else "empty"
    return {
        "status": status,
        "issue_date": issue_date,
        "slot": slot,
        "fetch_status": fetch_status,
        "article_count": len(articles),
        "compose_mode": compose_mode,
        "compose_models": compose_models,
        "prior_digest_s3_key": prior_key,
        "s3": s3_out,
        "digest_preview": markdown[:1200],
    }
