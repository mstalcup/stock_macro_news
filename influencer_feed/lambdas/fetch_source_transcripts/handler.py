import json
import os
import random
import sys
import time
from datetime import datetime, timezone

import boto3

VENDOR_DIR = os.path.join(os.path.dirname(__file__), "vendor")
if os.path.isdir(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)
try:
    from youtube_transcript_api import YouTubeTranscriptApi
    from youtube_transcript_api.proxies import GenericProxyConfig
except Exception:  # pragma: no cover
    YouTubeTranscriptApi = None
    GenericProxyConfig = None

TABLE_NAME = os.environ["TABLE_NAME"]
TRANSCRIPT_BUCKET = os.environ["TRANSCRIPT_BUCKET"]
WEBSHARE_SECRET_ARN = (os.environ.get("WEBSHARE_SECRET_ARN") or "").strip()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _prefix(*, user_id: str, issue_date: str, source_id: str) -> str:
    return f"v1/user/{user_id}/issue/{issue_date}/source/{source_id}/"


def _video_key(prefix: str, video_id: str) -> str:
    return f"{prefix}video/{video_id}.json"


def _manifest_key(prefix: str) -> str:
    return f"{prefix}manifest.json"


def _parse_proxy_url_entry(raw: str) -> str:
    line = (raw or "").strip()
    if not line:
        return ""
    if line.startswith("http://") or line.startswith("https://"):
        return line
    parts = line.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        return f"http://{user}:{password}@{host}:{port}"
    return ""


def _load_proxy_urls() -> list[str]:
    if not WEBSHARE_SECRET_ARN:
        return []
    sm = boto3.client("secretsmanager")
    raw = (sm.get_secret_value(SecretId=WEBSHARE_SECRET_ARN).get("SecretString") or "").strip()
    if not raw:
        return []
    # JSON forms:
    # {"proxy_url":"http://user:pass@host:port"}
    # {"proxy_urls":["http://u:p@h1:port","http://u:p@h2:port"]}
    # {"webshare_proxy_url":"http://user:pass@host:port"}
    # also accepts host:port:user:pass entries for convenience
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        urls = []
        one = (data.get("proxy_url") or data.get("webshare_proxy_url") or "").strip()
        if one:
            parsed = _parse_proxy_url_entry(one)
            if parsed:
                urls.append(parsed)
        many = data.get("proxy_urls") or []
        if isinstance(many, list):
            for v in many:
                s = str(v).strip()
                parsed = _parse_proxy_url_entry(s)
                if parsed:
                    urls.append(parsed)
        # de-dup preserve order
        out = []
        seen = set()
        for u in urls:
            if u not in seen:
                out.append(u)
                seen.add(u)
        return out
    # raw single URL fallback
    parsed = _parse_proxy_url_entry(raw)
    return [parsed] if parsed else []


def _chunks_to_text(chunks) -> str:
    parts = []
    for chunk in chunks:
        text = getattr(chunk, "text", None)
        if text is None and isinstance(chunk, dict):
            text = chunk.get("text", "")
        text = (text or "").replace("\n", " ").strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def _fetch_transcript_text(video_id: str, api: YouTubeTranscriptApi | None = None) -> tuple[str, str, str]:
    if YouTubeTranscriptApi is None:
        return "", "none", "youtube_transcript_api import failed"
    try:
        transcript_list = (api or YouTubeTranscriptApi()).list(video_id)
    except Exception as exc:
        return "", "none", f"list transcripts failed: {type(exc).__name__}: {exc}"

    # 1) explicit English
    try:
        t = transcript_list.find_transcript(["en", "en-US", "en-GB"])
        text = _chunks_to_text(t.fetch())
        if text:
            return text, "caption:en", ""
    except Exception:
        pass

    # 2) generated English
    try:
        t = transcript_list.find_generated_transcript(["en", "en-US", "en-GB"])
        text = _chunks_to_text(t.fetch())
        if text:
            return text, "caption:en-generated", ""
    except Exception:
        pass

    # 3) any transcript, translated to English if possible
    try:
        for t in transcript_list:
            try:
                if getattr(t, "is_translatable", False):
                    text = _chunks_to_text(t.translate("en").fetch())
                    if text:
                        return text, "caption:any->en", ""
                text = _chunks_to_text(t.fetch())
                if text:
                    return text, "caption:any", ""
            except Exception:
                continue
    except Exception:
        pass
    return "", "none", "no transcript available"


def _fetch_transcript_text_with_retry(
    video_id: str,
    max_attempts: int = 3,
    proxy_urls: list[str] | None = None,
) -> tuple[str, str, str]:
    last_err = ""
    proxy_urls = proxy_urls or []
    if proxy_urls:
        random.shuffle(proxy_urls)
        print(f"transcript fetch for {video_id}: proxy rotation enabled ({len(proxy_urls)} proxies)")
    for attempt in range(1, max_attempts + 1):
        api = None
        if proxy_urls and GenericProxyConfig is not None:
            proxy = proxy_urls[(attempt - 1) % len(proxy_urls)]
            try:
                proxy_config = GenericProxyConfig(http_url=proxy, https_url=proxy)
                api = YouTubeTranscriptApi(proxy_config=proxy_config)
            except Exception as exc:
                print(f"proxy init failed for attempt {attempt}: {type(exc).__name__}: {exc}")
        text, label, err = _fetch_transcript_text(video_id, api=api)
        if text:
            return text, label, ""
        last_err = err
        print(f"transcript attempt {attempt}/{max_attempts} failed for {video_id}: {last_err}")
        if attempt < max_attempts:
            time.sleep(1.0 * (2 ** (attempt - 1)))
    return "", "none", last_err or "no transcript available"


def handler(event, context):
    issue_date = event["issue_date"]
    user_id = event.get("user_id", "default")
    source_id = event["source_id"]
    ingest_status = event.get("ingest_status", "")

    # Fast skip for NO_NEW sources from ingest map.
    if ingest_status and ingest_status != "FETCHED":
        return {
            "source_id": source_id,
            "transcript_status": "SKIPPED_NO_NEW",
            "video_count": 0,
        }

    pk = f"USER#{user_id}"
    sk = f"FETCH#{issue_date}#{source_id}"
    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    fetch_row = table.get_item(Key={"pk": pk, "sk": sk}).get("Item")
    if not fetch_row:
        return {"source_id": source_id, "transcript_status": "MISSING_FETCH_ROW", "video_count": 0}

    status = fetch_row.get("status", "")
    if status != "FETCHED":
        return {"source_id": source_id, "transcript_status": "SKIPPED_NO_NEW", "video_count": 0}

    video_ids = fetch_row.get("video_ids") or []
    if not video_ids:
        table.update_item(
            Key={"pk": pk, "sk": sk},
            UpdateExpression="SET transcript_status = :s, transcript_updated_at = :t",
            ExpressionAttributeValues={":s": "NO_VIDEOS", ":t": _utc_now_iso()},
        )
        return {"source_id": source_id, "transcript_status": "NO_VIDEOS", "video_count": 0}

    s3 = boto3.client("s3")
    proxy_urls = _load_proxy_urls()
    pfx = _prefix(user_id=user_id, issue_date=issue_date, source_id=source_id)
    manifest_videos = []
    ok_count = 0
    for video_id in video_ids:
        text, label, err = _fetch_transcript_text_with_retry(
            video_id,
            max_attempts=4,
            proxy_urls=proxy_urls,
        )
        key = _video_key(pfx, video_id)
        payload = {
            "schema_version": 1,
            "user_id": user_id,
            "issue_date": issue_date,
            "source_id": source_id,
            "video_id": video_id,
            "video_url": f"https://www.youtube.com/watch?v={video_id}",
            "fetched_at": _utc_now_iso(),
            "transcript_source": label if text else "none",
            "transcript_error": err if not text else "",
            "transcript_text": text,
        }
        s3.put_object(
            Bucket=TRANSCRIPT_BUCKET,
            Key=key,
            Body=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
            ContentType="application/json; charset=utf-8",
        )
        manifest_videos.append(
            {
                "video_id": video_id,
                "s3_key": key,
                "status": "uploaded" if text else "empty",
                "transcript_source": label if text else "none",
            }
        )
        if text:
            ok_count += 1

    manifest = {
        "schema_version": 1,
        "user_id": user_id,
        "issue_date": issue_date,
        "source_id": source_id,
        "bucket": TRANSCRIPT_BUCKET,
        "prefix": pfx,
        "videos": manifest_videos,
        "updated_at": _utc_now_iso(),
    }
    mkey = _manifest_key(pfx)
    s3.put_object(
        Bucket=TRANSCRIPT_BUCKET,
        Key=mkey,
        Body=json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )

    overall = "FETCHED" if ok_count > 0 else "EMPTY"
    table.update_item(
        Key={"pk": pk, "sk": sk},
        UpdateExpression=(
            "SET transcript_status = :s, transcript_s3_bucket = :b, transcript_s3_prefix = :p, "
            "transcript_manifest_s3_key = :m, transcript_fetched_at = :t, transcript_video_count = :c"
        ),
        ExpressionAttributeValues={
            ":s": overall,
            ":b": TRANSCRIPT_BUCKET,
            ":p": pfx,
            ":m": mkey,
            ":t": _utc_now_iso(),
            ":c": len(video_ids),
        },
    )
    return {
        "source_id": source_id,
        "transcript_status": overall,
        "video_count": len(video_ids),
        "ok_count": ok_count,
    }
