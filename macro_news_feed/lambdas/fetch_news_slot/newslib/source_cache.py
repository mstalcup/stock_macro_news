"""Read per-provider headline JSON from S3 when already present for a date/slot."""

from __future__ import annotations

import json

import boto3
from botocore.exceptions import ClientError

from .storage import provider_source_key


def load_cached_provider_articles(
    *,
    bucket: str,
    issue_date: str,
    slot: str,
    provider: str,
) -> tuple[list[dict], dict] | None:
    """
  Return (articles, cache_meta) when by_source/{provider}.json exists with items.
  cache_meta includes fetched_at, s3_key, article_count from the stored object.
    """
    key = provider_source_key(issue_date=issue_date, slot=slot, provider=provider)
    s3 = boto3.client("s3")
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            return None
        raise

    doc = json.loads(body)
    articles = doc.get("articles") or []
    if not articles:
        return None

    return articles, {
        "s3_key": key,
        "fetched_at": doc.get("fetched_at"),
        "article_count": len(articles),
        "cached_at_read": doc.get("fetched_at"),
    }
