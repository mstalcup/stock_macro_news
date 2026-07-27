from __future__ import annotations

import json
from datetime import date, timedelta

import boto3
from botocore.exceptions import ClientError


def slot_prefix(*, issue_date: str, slot: str) -> str:
    return f"v1/date={issue_date}/slot={slot}/"


def read_json(*, bucket: str, key: str) -> dict | None:
    s3 = boto3.client("s3")
    try:
        body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        return json.loads(body)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404", "NotFound"):
            return None
        raise


def put_json(*, bucket: str, key: str, payload: dict) -> None:
    s3 = boto3.client("s3")
    s3.put_object(
        Bucket=bucket,
        Key=key,
        Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )


def load_deduped(*, bucket: str, issue_date: str, slot: str, deduped_s3_key: str | None) -> dict:
    key = deduped_s3_key or f"{slot_prefix(issue_date=issue_date, slot=slot)}deduped.json"
    doc = read_json(bucket=bucket, key=key)
    if not doc:
        raise FileNotFoundError(f"deduped not found: s3://{bucket}/{key}")
    return doc


def prior_digest_keys(*, issue_date: str, slot: str) -> list[str]:
    """
    Earlier digest for comparison:
    - pre_close: same-day pre_open, then prior-day pre_close
    - pre_open: prior-day pre_close, then prior-day pre_open
    """
    keys: list[str] = []
    try:
        prev_day = (date.fromisoformat(issue_date) - timedelta(days=1)).isoformat()
    except ValueError:
        prev_day = ""

    if slot == "pre_close":
        keys.append(f"{slot_prefix(issue_date=issue_date, slot='pre_open')}digest.json")
        if prev_day:
            keys.append(f"{slot_prefix(issue_date=prev_day, slot='pre_close')}digest.json")
    else:
        if prev_day:
            keys.append(f"{slot_prefix(issue_date=prev_day, slot='pre_close')}digest.json")
            keys.append(f"{slot_prefix(issue_date=prev_day, slot='pre_open')}digest.json")
    return keys


def load_prior_digest_text(*, bucket: str, issue_date: str, slot: str) -> tuple[str, str | None]:
    for key in prior_digest_keys(issue_date=issue_date, slot=slot):
        doc = read_json(bucket=bucket, key=key)
        if not doc:
            continue
        digest = doc.get("digest") or {}
        summary = (digest.get("executive_summary") or "").strip()
        if not summary:
            summary = (doc.get("digest_markdown") or "").strip()
        if summary:
            return summary[:4000], key
    return "", None


def write_digest_artifacts(
    *,
    bucket: str,
    issue_date: str,
    slot: str,
    deduped_s3_key: str,
    deduped_count: int,
    digest_body: dict,
    compose_meta: dict,
) -> dict:
    pfx = slot_prefix(issue_date=issue_date, slot=slot)
    digest_key = f"{pfx}digest.json"
    payload = {
        "schema_version": 1,
        "issue_date": issue_date,
        "slot": slot,
        "composed_at": compose_meta.get("composed_at"),
        "source": {
            "deduped_s3_key": deduped_s3_key,
            "article_count": deduped_count,
        },
        "compose_mode": compose_meta.get("compose_mode"),
        "compose_models": compose_meta.get("compose_models"),
        "prior_digest_s3_key": compose_meta.get("prior_digest_s3_key"),
        "article_notes": compose_meta.get("article_notes") or [],
        "digest": digest_body,
        "digest_markdown": compose_meta.get("digest_markdown") or "",
    }
    put_json(bucket=bucket, key=digest_key, payload=payload)

    manifest_key = f"{pfx}manifest.json"
    manifest = read_json(bucket=bucket, key=manifest_key) or {
        "schema_version": 1,
        "issue_date": issue_date,
        "slot": slot,
        "bucket": bucket,
        "prefix": pfx,
    }
    manifest["digest"] = {
        "s3_key": digest_key,
        "compose_mode": compose_meta.get("compose_mode"),
        "composed_at": compose_meta.get("composed_at"),
    }
    put_json(bucket=bucket, key=manifest_key, payload=manifest)

    latest_key = f"v1/latest/slot={slot}.json"
    latest = read_json(bucket=bucket, key=latest_key) or {}
    latest.update(
        {
            "schema_version": 1,
            "issue_date": issue_date,
            "slot": slot,
            "digest_s3_key": digest_key,
            "digest_composed_at": compose_meta.get("composed_at"),
        }
    )
    put_json(bucket=bucket, key=latest_key, payload=latest)

    return {"digest_s3_key": digest_key, "manifest_s3_key": manifest_key, "latest_s3_key": latest_key}
