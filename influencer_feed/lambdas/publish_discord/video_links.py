"""Video links for Discord — only videos with a stored transcript on this FETCH row."""
from __future__ import annotations

import json


def _transcript_ok_by_video(fetch_row: dict) -> dict[str, bool]:
    out: dict[str, bool] = {}
    bucket = fetch_row.get("transcript_s3_bucket")
    manifest_key = fetch_row.get("transcript_manifest_s3_key")
    if not bucket or not manifest_key:
        return out
    import boto3

    try:
        raw = boto3.client("s3").get_object(Bucket=bucket, Key=manifest_key)["Body"].read().decode("utf-8")
        manifest = json.loads(raw)
    except Exception:
        return out
    for v in manifest.get("videos") or []:
        vid = (v.get("video_id") or "").strip()
        if vid:
            out[vid] = v.get("status") == "uploaded"
    return out


def links_for_report(*, fetch_row: dict, **_kwargs) -> list[str]:
    transcript_ok = _transcript_ok_by_video(fetch_row)
    urls: list[str] = []
    for m in fetch_row.get("video_meta") or []:
        if not isinstance(m, dict):
            continue
        vid = (m.get("video_id") or "").strip()
        url = (m.get("video_url") or "").strip()
        if vid and url and transcript_ok.get(vid):
            urls.append(url)
    return urls
