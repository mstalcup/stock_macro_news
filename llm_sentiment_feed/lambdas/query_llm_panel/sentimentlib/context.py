from __future__ import annotations

import json

import boto3
from botocore.exceptions import ClientError


def load_macro_slot(*, macro_bucket: str, issue_date: str, slot: str = "pre_open") -> dict:
    s3 = boto3.client("s3")
    digest_key = f"v1/date={issue_date}/slot={slot}/digest.json"
    deduped_key = f"v1/date={issue_date}/slot={slot}/deduped.json"
    digest = _read_json(s3, macro_bucket, digest_key) or {}
    deduped = _read_json(s3, macro_bucket, deduped_key) or {}
    return {
        "digest_key": digest_key,
        "deduped_key": deduped_key,
        "digest_markdown": digest.get("digest_markdown") or "",
        "digest": digest.get("digest") or {},
        "headlines": deduped.get("articles") or [],
    }


def _read_json(s3, bucket: str, key: str) -> dict | None:
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("NoSuchKey", "404"):
            return None
        raise
