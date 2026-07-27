import json
from datetime import datetime, timezone

import boto3


def slot_prefix(*, issue_date: str, slot: str) -> str:
    return f"v1/date={issue_date}/slot={slot}/"


def provider_source_key(*, issue_date: str, slot: str, provider: str) -> str:
    return f"{slot_prefix(issue_date=issue_date, slot=slot)}by_source/{provider}.json"


def write_slot_artifacts(
    *,
    bucket: str,
    issue_date: str,
    slot: str,
    by_provider: dict[str, list[dict]],
    deduped: list[dict],
    dedupe_stats: dict,
    fetch_meta: dict,
    skip_source_write: frozenset[str] | None = None,
) -> dict:
    s3 = boto3.client("s3")
    pfx = slot_prefix(issue_date=issue_date, slot=slot)
    now = datetime.now(timezone.utc).isoformat()
    skip = skip_source_write or frozenset()
    cache_meta = fetch_meta.get("source_cache") or {}

    source_keys = {}
    for provider, articles in by_provider.items():
        key = provider_source_key(issue_date=issue_date, slot=slot, provider=provider)
        source_keys[provider] = key
        if provider in skip:
            continue
        body = {
            "schema_version": 1,
            "provider": provider,
            "issue_date": issue_date,
            "slot": slot,
            "fetched_at": now,
            "article_count": len(articles),
            "articles": articles,
            "error": fetch_meta.get("errors", {}).get(provider),
        }
        _put_json(s3, bucket, key, body)

    deduped_key = f"{pfx}deduped.json"
    deduped_body = {
        "schema_version": 1,
        "issue_date": issue_date,
        "slot": slot,
        "fetched_at": now,
        "article_count": len(deduped),
        "dedupe_stats": dedupe_stats,
        "articles": deduped,
    }
    _put_json(s3, bucket, deduped_key, deduped_body)

    manifest_key = f"{pfx}manifest.json"
    manifest = {
        "schema_version": 1,
        "issue_date": issue_date,
        "slot": slot,
        "slot_label": fetch_meta.get("slot_label", slot),
        "fetched_at": now,
        "timezone": fetch_meta.get("timezone", "America/Los_Angeles"),
        "window_start_utc": fetch_meta.get("window_start_utc"),
        "window_end_utc": fetch_meta.get("window_end_utc"),
        "bucket": bucket,
        "prefix": pfx,
        "sources": {
            provider: {
                "article_count": len(by_provider.get(provider, [])),
                "s3_key": source_keys.get(provider),
                "error": fetch_meta.get("errors", {}).get(provider),
                "cached": bool((cache_meta.get(provider) or {}).get("from_s3")),
                "cached_fetched_at": (cache_meta.get(provider) or {}).get("fetched_at"),
            }
            for provider in ("alpha_vantage", "newsapi", "finnhub")
        },
        "deduped": {
            "article_count": len(deduped),
            "s3_key": deduped_key,
            "stats": dedupe_stats,
        },
    }
    _put_json(s3, bucket, manifest_key, manifest)

    latest_key = f"v1/latest/slot={slot}.json"
    _put_json(
        s3,
        bucket,
        latest_key,
        {
            "schema_version": 1,
            "issue_date": issue_date,
            "slot": slot,
            "manifest_s3_key": manifest_key,
            "deduped_s3_key": deduped_key,
            "deduped_count": len(deduped),
            "fetched_at": now,
        },
    )

    return {
        "manifest_s3_key": manifest_key,
        "deduped_s3_key": deduped_key,
        "latest_s3_key": latest_key,
        "source_s3_keys": source_keys,
    }


def _put_json(s3, bucket: str, key: str, payload: dict) -> None:
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )
