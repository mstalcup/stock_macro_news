"""
Fetch macro headlines for a scheduled slot (pre_open / pre_close).

Writes per-source JSON + deduped merge + manifest under:
  s3://{bucket}/v1/date={YYYY-MM-DD}/slot={pre_open|pre_close}/

Per-provider files in by_source/ are reused from S3 when present (cache).
Pass force_refresh=true to re-pull all enabled APIs.
"""
from __future__ import annotations

import os
from datetime import datetime
from zoneinfo import ZoneInfo

from newslib.config import ENABLED_PROVIDERS, SLOT_LABELS, USE_S3_SOURCE_CACHE
from newslib.dedupe import dedupe_articles
from newslib.fetchers import fetch_alpha_vantage, fetch_finnhub, fetch_newsapi
from newslib.secrets import load_api_keys
from newslib.source_cache import load_cached_provider_articles
from newslib.storage import write_slot_artifacts
from newslib.window import fetch_window

BUCKET = os.environ["NEWS_ARTIFACTS_BUCKET"]
LOCAL_TZ = os.environ.get("SCHEDULE_LOCAL_TZ", "America/Los_Angeles")
VALID_SLOTS = frozenset({"pre_open", "pre_close"})


def _issue_date(event: dict) -> str:
    explicit = (event.get("issue_date") or "").strip()
    if explicit:
        return explicit
    return datetime.now(ZoneInfo(LOCAL_TZ)).date().isoformat()


def _use_cache(event: dict) -> bool:
    if event.get("force_refresh") is True:
        return False
    if str(event.get("force_refresh", "")).lower() in ("1", "true", "yes"):
        return False
    return USE_S3_SOURCE_CACHE


def _try_cache(*, issue_date: str, slot: str, provider: str) -> tuple[list[dict], dict] | None:
    hit = load_cached_provider_articles(
        bucket=BUCKET, issue_date=issue_date, slot=slot, provider=provider
    )
    if not hit:
        return None
    articles, meta = hit
    return articles, {"from_s3": True, "fetched_at": meta.get("fetched_at"), "s3_key": meta.get("s3_key")}


def handler(event, context):
    event = event or {}
    slot = (event.get("slot") or "").strip()
    if slot not in VALID_SLOTS:
        raise ValueError(f"slot must be one of {sorted(VALID_SLOTS)}, got {slot!r}")

    issue_date = _issue_date(event)
    window_start, window_end = fetch_window(issue_date, slot, LOCAL_TZ)
    use_cache = _use_cache(event)
    keys = load_api_keys()

    by_provider: dict[str, list[dict]] = {}
    errors: dict[str, str | None] = {}
    source_cache: dict[str, dict] = {}
    cached_providers: set[str] = set()

    def _load_or_fetch(provider: str, fetch_fn) -> None:
        if use_cache:
            cached = _try_cache(issue_date=issue_date, slot=slot, provider=provider)
            if cached:
                articles, meta = cached
                by_provider[provider] = articles
                errors[provider] = None
                source_cache[provider] = meta
                cached_providers.add(provider)
                return
        articles, err = fetch_fn()
        by_provider[provider] = articles
        errors[provider] = err

    if "alpha_vantage" in ENABLED_PROVIDERS:
        _load_or_fetch(
            "alpha_vantage",
            lambda: fetch_alpha_vantage(
                keys["alpha_vantage"], window_start=window_start, window_end=window_end
            ),
        )
    else:
        by_provider["alpha_vantage"] = []
        errors["alpha_vantage"] = "disabled in ENABLED_PROVIDERS"

    if "newsapi" in ENABLED_PROVIDERS:
        _load_or_fetch(
            "newsapi",
            lambda: fetch_newsapi(keys["newsapi"], window_start=window_start, window_end=window_end),
        )
    else:
        by_provider["newsapi"] = []
        errors["newsapi"] = "disabled in ENABLED_PROVIDERS"

    if "finnhub" in ENABLED_PROVIDERS:
        _load_or_fetch(
            "finnhub",
            lambda: fetch_finnhub(keys["finnhub"], window_start=window_start, window_end=window_end),
        )
    else:
        by_provider["finnhub"] = []
        errors["finnhub"] = "disabled in ENABLED_PROVIDERS"

    deduped, dedupe_stats = dedupe_articles(by_provider)

    s3_keys = write_slot_artifacts(
        bucket=BUCKET,
        issue_date=issue_date,
        slot=slot,
        by_provider=by_provider,
        deduped=deduped,
        dedupe_stats=dedupe_stats,
        skip_source_write=frozenset(cached_providers),
        fetch_meta={
            "timezone": LOCAL_TZ,
            "slot_label": SLOT_LABELS.get(slot, slot),
            "window_start_utc": window_start.isoformat(),
            "window_end_utc": window_end.isoformat(),
            "errors": errors,
            "source_cache": source_cache,
            "cache_enabled": use_cache,
            "cached_providers": sorted(cached_providers),
        },
    )

    total_raw = sum(len(v) for v in by_provider.values())
    status = "ok" if len(deduped) > 0 else ("partial" if total_raw > 0 else "empty")

    return {
        "status": status,
        "issue_date": issue_date,
        "slot": slot,
        "counts": {k: len(v) for k, v in by_provider.items()} | {"deduped": len(deduped)},
        "enabled_providers": list(ENABLED_PROVIDERS),
        "cached_providers": sorted(cached_providers),
        "cache_enabled": use_cache,
        "errors": {k: v for k, v in errors.items() if v},
        "s3": s3_keys,
    }

