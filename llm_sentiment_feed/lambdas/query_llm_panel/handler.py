"""
Query a panel of LLMs for 30-day equity picks (hybrid context from macro digest).
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

import boto3

from sentimentlib.config import MIN_PICKS, PANEL_MODELS, PROMPT_VERSION
from sentimentlib.context import load_macro_slot
from sentimentlib.prompts import build_context_pack
from sentimentlib.providers import query_model
from sentimentlib.secrets import load_keys
from sentimentlib.storage import issue_pk, model_sk, put_model_result

MACRO_BUCKET = os.environ["MACRO_ARTIFACTS_BUCKET"]
LOCAL_TZ = os.environ.get("SCHEDULE_LOCAL_TZ", "America/Los_Angeles")
TABLE_NAME = os.environ["SENTIMENT_TABLE_NAME"]


def _issue_date(event: dict) -> str:
    explicit = (event.get("issue_date") or "").strip()
    if explicit:
        return explicit
    return datetime.now(ZoneInfo(LOCAL_TZ)).date().isoformat()


def _use_context(event: dict) -> bool:
    if event.get("use_context") is False:
        return False
    if str(event.get("use_context", "")).lower() in ("0", "false", "no"):
        return False
    return True


def _force_refresh(event: dict) -> bool:
    return event.get("force_refresh") is True or str(
        event.get("force_refresh", "")
    ).lower() in ("1", "true", "yes")


def _model_cached(issue_date: str, model_id: str) -> bool:
    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    item = table.get_item(Key={"pk": issue_pk(issue_date), "sk": model_sk(model_id)}).get(
        "Item"
    )
    if not item or item.get("status") != "ok":
        return False
    if item.get("prompt_version") != PROMPT_VERSION:
        return False
    return int(item.get("pick_count") or 0) >= MIN_PICKS


def handler(event, context):
    event = event or {}
    issue_date = _issue_date(event)
    slot = (event.get("macro_slot") or "pre_open").strip()
    use_context = _use_context(event)
    force = _force_refresh(event)

    macro = load_macro_slot(macro_bucket=MACRO_BUCKET, issue_date=issue_date, slot=slot)
    if use_context and not macro.get("digest_markdown") and not macro.get("headlines"):
        raise FileNotFoundError(
            f"No macro digest for {issue_date} slot={slot} in s3://{MACRO_BUCKET}"
        )

    context_pack = build_context_pack(
        digest_markdown=macro.get("digest_markdown") or "(no digest)",
        headlines=macro.get("headlines") or [],
    )
    context_meta = {
        "macro_bucket": MACRO_BUCKET,
        "macro_slot": slot,
        "digest_key": macro.get("digest_key"),
        "deduped_key": macro.get("deduped_key"),
        "headline_count": len(macro.get("headlines") or []),
    }

    keys = load_keys()
    results = []
    errors = {}

    for spec in PANEL_MODELS:
        model_id = spec["model_id"]
        provider = spec["provider"]
        api_key = keys.get(provider) or ""
        if not api_key:
            errors[model_id] = f"missing {provider} api key"
            put_model_result(
                issue_date=issue_date,
                model_id=model_id,
                provider=provider,
                prompt_version=PROMPT_VERSION,
                use_context=use_context,
                result={"market_bias": "unclear", "picks": []},
                raw_response="",
                context_meta=context_meta,
                error=errors[model_id],
            )
            continue

        if not force and _model_cached(issue_date, model_id):
            results.append({"model_id": model_id, "status": "cached"})
            continue

        try:
            parsed, raw = query_model(
                provider=provider,
                api_model=spec["api_model"],
                api_key=api_key,
                issue_date=issue_date,
                context_pack=context_pack,
                use_context=use_context,
            )
            meta = put_model_result(
                issue_date=issue_date,
                model_id=model_id,
                provider=provider,
                prompt_version=PROMPT_VERSION,
                use_context=use_context,
                result=parsed,
                raw_response=raw,
                context_meta=context_meta,
            )
            results.append(
                {
                    "model_id": model_id,
                    "status": "ok",
                    "pick_count": meta["pick_count"],
                    "market_bias": parsed.get("market_bias"),
                }
            )
        except Exception as exc:
            err = f"{type(exc).__name__}: {exc}"
            errors[model_id] = err
            put_model_result(
                issue_date=issue_date,
                model_id=model_id,
                provider=provider,
                prompt_version=PROMPT_VERSION,
                use_context=use_context,
                result={"market_bias": "unclear", "picks": []},
                raw_response="",
                context_meta=context_meta,
                error=err,
            )
            results.append({"model_id": model_id, "status": "error", "error": err[:200]})

    ok = sum(1 for r in results if r.get("status") == "ok")
    return {
        "status": "ok" if ok else "partial",
        "issue_date": issue_date,
        "macro_slot": slot,
        "use_context": use_context,
        "prompt_version": PROMPT_VERSION,
        "models": results,
        "errors": errors,
        "test_run": (os.environ.get("TEST_RUN") or "").lower() in ("1", "true", "yes"),
    }
