import os
import json
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from xml.etree import ElementTree as ET

import boto3

VENDOR_DIR = os.path.join(os.path.dirname(__file__), "vendor")
if os.path.isdir(VENDOR_DIR) and VENDOR_DIR not in sys.path:
    sys.path.insert(0, VENDOR_DIR)
try:
    from youtube_transcript_api import YouTubeTranscriptApi
except Exception:  # pragma: no cover - handled by fallback behavior below.
    YouTubeTranscriptApi = None

TABLE_NAME = os.environ["TABLE_NAME"]
WEBSHARE_SECRET_ARN = (os.environ.get("WEBSHARE_SECRET_ARN") or "").strip()
NY_TZ = "America/New_York"
YOUTUBE_NS = {"a": "http://www.w3.org/2005/Atom", "m": "http://search.yahoo.com/mrss/"}


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
    if raw.startswith("{"):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            return []
        urls: list[str] = []
        one = (data.get("proxy_url") or data.get("webshare_proxy_url") or "").strip()
        if one:
            p = _parse_proxy_url_entry(one)
            if p:
                urls.append(p)
        many = data.get("proxy_urls") or []
        if isinstance(many, list):
            for v in many:
                p = _parse_proxy_url_entry(str(v).strip())
                if p:
                    urls.append(p)
        out: list[str] = []
        seen: set[str] = set()
        for u in urls:
            if u not in seen:
                out.append(u)
                seen.add(u)
        return out
    p = _parse_proxy_url_entry(raw)
    return [p] if p else []


def _urlopen_with_proxy_fallback(req: urllib.request.Request, *, timeout: int = 20):
    """Direct first; on typical block/transient responses retry via Webshare pool (same secret as transcripts)."""
    proxy_urls = _load_proxy_urls()
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code not in (403, 404, 429, 500, 502, 503) or not proxy_urls:
            raise
    except urllib.error.URLError:
        if not proxy_urls:
            raise
    proxies = list(proxy_urls)
    random.shuffle(proxies)
    last_exc: BaseException | None = None
    for proxy_url in proxies[:10]:
        try:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
            )
            return opener.open(req, timeout=timeout)
        except BaseException as exc:
            last_exc = exc
            continue
    if last_exc is not None:
        raise last_exc
    raise urllib.error.URLError("proxy fallback exhausted")


def _parse_issue_window(issue_date):
    # Keep ET calendar-day boundaries, matching your issue date model.
    from zoneinfo import ZoneInfo

    local_start = datetime.fromisoformat(f"{issue_date}T00:00:00").replace(tzinfo=ZoneInfo(NY_TZ))
    local_end = local_start + timedelta(days=1)
    return local_start.astimezone(timezone.utc), local_end.astimezone(timezone.utc)


def _extract_channel_id_from_handle(channel_handle):
    if not channel_handle:
        return None
    handle = channel_handle if channel_handle.startswith("@") else f"@{channel_handle}"
    # Prefer oEmbed lookup first; it's usually stable for handles.
    oembed_url = (
        "https://www.youtube.com/oembed?format=json&url="
        + urllib.parse.quote(f"https://www.youtube.com/{handle}", safe="")
    )
    try:
        req = urllib.request.Request(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
        with _urlopen_with_proxy_fallback(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
        author_url = payload.get("author_url", "")
        m = re.search(r"/channel/(UC[\w-]+)", author_url)
        if m:
            return m.group(1)
    except Exception:
        pass

    url = f"https://www.youtube.com/{handle}"
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with _urlopen_with_proxy_fallback(req, timeout=20) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
    for pattern in (
        r'rel="canonical"\s+href="https://www\.youtube\.com/channel/(UC[\w-]+)"',
        r'"externalId":"(UC[^"]+)"',
        r'"channelId":"(UC[^"]+)"',
    ):
        m = re.search(pattern, body)
        if m:
            return m.group(1)
    return None


def _fetch_feed_entries(channel_id):
    feed_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={urllib.parse.quote(channel_id)}"
    req = urllib.request.Request(feed_url, headers={"User-Agent": "Mozilla/5.0"})
    with _urlopen_with_proxy_fallback(req, timeout=20) as resp:
        root = ET.fromstring(resp.read())
    entries = []
    for entry in root.findall("a:entry", YOUTUBE_NS):
        vid = entry.findtext("yt:videoId", default=None, namespaces={"yt": "http://www.youtube.com/xml/schemas/2015"})
        if not vid:
            continue
        published_raw = entry.findtext("a:published", default="", namespaces=YOUTUBE_NS)
        if not published_raw:
            continue
        published_at = datetime.fromisoformat(published_raw.replace("Z", "+00:00"))
        title = entry.findtext("a:title", default="", namespaces=YOUTUBE_NS)
        link = entry.find("a:link", YOUTUBE_NS)
        video_url = link.attrib.get("href") if link is not None else f"https://www.youtube.com/watch?v={vid}"
        description = entry.findtext("m:group/m:description", default="", namespaces=YOUTUBE_NS)
        entries.append(
            {
                "video_id": vid,
                "published_at": published_at,
                "title": title,
                "video_url": video_url,
                "description": description,
            }
        )
    return entries


def _http_get(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept-Language": "en-US,en;q=0.9",
        },
    )
    with _urlopen_with_proxy_fallback(req, timeout=timeout) as resp:
        return resp.read().decode("utf-8", errors="ignore")


def _chunks_to_text(chunks):
    parts = []
    for chunk in chunks:
        text = getattr(chunk, "text", None)
        if text is None and isinstance(chunk, dict):
            text = chunk.get("text", "")
        text = (text or "").replace("\n", " ").strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def _get_transcript_text(video_id: str) -> tuple[str, str, str]:
    """
    Returns (text, source_tag) where source_tag identifies transcript type.
    """
    if YouTubeTranscriptApi is None:
        return "", "", "youtube_transcript_api import failed"
    last_error = ""
    try:
        transcript_list = YouTubeTranscriptApi().list(video_id)
    except Exception as exc:
        return "", "", f"list transcripts failed: {type(exc).__name__}: {exc}"

    # 1) Explicit English transcript.
    try:
        t = transcript_list.find_transcript(["en", "en-US", "en-GB"])
        text = _chunks_to_text(t.fetch())
        if text:
            return text, "caption:en", ""
    except Exception as exc:
        last_error = f"find en transcript failed: {type(exc).__name__}: {exc}"

    # 2) Generated English transcript.
    try:
        t = transcript_list.find_generated_transcript(["en", "en-US", "en-GB"])
        text = _chunks_to_text(t.fetch())
        if text:
            return text, "caption:en-generated", ""
    except Exception as exc:
        last_error = f"find generated en transcript failed: {type(exc).__name__}: {exc}"

    # 3) Chinese transcript translated to English when available.
    for lang in ("zh-Hans", "zh-Hant", "zh", "zh-CN", "zh-TW"):
        try:
            t = transcript_list.find_transcript([lang])
            if getattr(t, "is_translatable", False):
                text = _chunks_to_text(t.translate("en").fetch())
                if text:
                    return text, "caption:zh->en", ""
            text = _chunks_to_text(t.fetch())
            if text:
                return text, "caption:zh", ""
        except Exception as exc:
            last_error = f"find zh transcript failed ({lang}): {type(exc).__name__}: {exc}"
            continue

    # 4) Last resort: any transcript, translated to English if possible.
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
            except Exception as exc:
                last_error = f"iterate transcript failed: {type(exc).__name__}: {exc}"
                continue
    except Exception:
        pass
    return "", "", last_error


def handler(event, context):
    issue_date = event["issue_date"]
    user_id = event.get("user_id", "default")
    source = event["source"]
    source_id = source["source_id"]
    platform = source.get("platform", "youtube")
    channel_id = source.get("channel_id") or _extract_channel_id_from_handle(source.get("channel_handle"))
    pk = f"USER#{user_id}"
    sk = f"FETCH#{issue_date}#{source_id}"
    now = datetime.now(timezone.utc).isoformat()

    if platform != "youtube":
        raise ValueError(f"Unsupported platform in ingest_source: {platform}")
    if not channel_id:
        raise ValueError(f"Missing channel_id/channel_handle for source={source_id}")

    window_start_utc, window_end_utc = _parse_issue_window(issue_date)
    entries = _fetch_feed_entries(channel_id)
    in_window = [
        entry
        for entry in entries
        if window_start_utc <= entry["published_at"].astimezone(timezone.utc) < window_end_utc
    ]

    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    if not in_window:
        table.put_item(
            Item={
                "pk": pk,
                "sk": sk,
                "issue_date": issue_date,
                "source_id": source_id,
                "platform": platform,
                "channel_id": channel_id,
                "status": "NO_NEW",
                "video_count": 0,
                "video_ids": [],
                "updated_at": now,
            }
        )
        return {"status": "NO_NEW", "source_id": source_id, "video_count": 0}

    video_ids = []
    for entry in in_window:
        video_ids.append(entry["video_id"])
    video_meta = [
        {
            "video_id": entry["video_id"],
            "title": entry["title"],
            "published_at_utc": entry["published_at"].astimezone(timezone.utc).isoformat(),
            "video_url": entry["video_url"],
        }
        for entry in in_window
    ]

    table.put_item(
        Item={
            "pk": pk,
            "sk": sk,
            "issue_date": issue_date,
            "source_id": source_id,
            "platform": platform,
            "channel_id": channel_id,
            "status": "FETCHED",
            "video_count": len(video_ids),
            "video_ids": video_ids,
            "video_meta": video_meta,
            "transcript_status": "PENDING_FETCH_TRANSCRIPT",
            "updated_at": now,
        }
    )
    return {"status": "FETCHED", "source_id": source_id, "video_count": len(video_ids)}
