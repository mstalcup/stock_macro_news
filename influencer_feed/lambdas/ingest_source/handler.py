import os
import json
import random
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
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


def _browser_headers() -> dict[str, str]:
    return {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept": "application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def _urlopen_with_proxy_fallback(req: urllib.request.Request, *, timeout: int = 20):
    """Direct first; on typical block/transient responses retry via Webshare pool (same secret as transcripts)."""
    proxy_urls = _load_proxy_urls()
    try:
        return urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        if exc.code not in (400, 403, 404, 429, 500, 502, 503) or not proxy_urls:
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
        req = urllib.request.Request(oembed_url, headers=_browser_headers())
        with _urlopen_with_proxy_fallback(req, timeout=20) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="ignore"))
        author_url = payload.get("author_url", "")
        m = re.search(r"/channel/(UC[\w-]+)", author_url)
        if m:
            return m.group(1)
    except Exception:
        pass

    url = f"https://www.youtube.com/{handle}"
    req = urllib.request.Request(url, headers=_browser_headers())
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


def _uploads_playlist_id(channel_id: str) -> str | None:
    cid = (channel_id or "").strip()
    if cid.startswith("UC") and len(cid) > 2:
        return "UU" + cid[2:]
    return None


def _read_feed_xml(feed_url: str) -> ET.Element:
    req = urllib.request.Request(feed_url, headers=_browser_headers())
    with _urlopen_with_proxy_fallback(req, timeout=20) as resp:
        return ET.fromstring(resp.read())


def _fetch_feed_entries_for_id(channel_id: str) -> list[dict]:
    urls = [f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"]
    upl = _uploads_playlist_id(channel_id)
    if upl:
        urls.append(f"https://www.youtube.com/feeds/videos.xml?playlist_id={upl}")
    last_exc: BaseException | None = None
    root = None
    for feed_url in urls:
        try:
            root = _read_feed_xml(feed_url)
            break
        except Exception as exc:
            last_exc = exc
            print(f"feed URL failed {feed_url}: {type(exc).__name__}: {exc}")
    if root is None:
        raise last_exc or RuntimeError(f"no feed for channel {channel_id}")
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


def _fetch_entries_from_channel_html(*, channel_id: str, channel_handle: str | None) -> list[dict]:
    """Fallback when Atom RSS returns 400 from AWS/proxy IPs."""
    if channel_handle:
        handle = channel_handle if channel_handle.startswith("@") else f"@{channel_handle}"
        page_url = f"https://www.youtube.com/{handle}/videos"
    else:
        page_url = f"https://www.youtube.com/channel/{channel_id}/videos"
    req = urllib.request.Request(page_url, headers=_browser_headers())
    with _urlopen_with_proxy_fallback(req, timeout=25) as resp:
        html = resp.read().decode("utf-8", errors="ignore")
    marker = "var ytInitialData = "
    if marker in html:
        start = html.index(marker) + len(marker)
        end = html.find(";</script>", start)
        if end > start:
            try:
                data = json.loads(html[start:end])
                entries = _entries_from_yt_initial_data(data)
                if entries:
                    return entries
            except Exception as exc:
                print(f"ytInitialData parse failed: {exc!r}")
    seen: set[str] = set()
    entries = []
    for vid in re.findall(r'"videoId":"([a-zA-Z0-9_-]{11})"', html):
        if vid in seen or vid.startswith("UC"):
            continue
        seen.add(vid)
        entries.append(
            {
                "video_id": vid,
                "published_at": datetime.now(timezone.utc),
                "title": "",
                "video_url": f"https://www.youtube.com/watch?v={vid}",
                "description": "",
            }
        )
        if len(entries) >= 15:
            break
    return entries


def _entries_from_yt_initial_data(data: dict) -> list[dict]:
    """Walk ytInitialData tabs to find recent video renderers."""
    out: list[dict] = []
    seen: set[str] = set()

    def walk(node):
        if isinstance(node, dict):
            vr = node.get("gridVideoRenderer") or node.get("videoRenderer")
            if isinstance(vr, dict):
                vid = (vr.get("videoId") or "").strip()
                if vid and vid not in seen and not vid.startswith("UC"):
                    seen.add(vid)
                    title = ""
                    if isinstance(vr.get("title"), dict):
                        title = (vr["title"].get("simpleText") or "").strip()
                        runs = vr["title"].get("runs")
                        if runs and isinstance(runs, list):
                            title = (runs[0].get("text") or "").strip()
                    pub = datetime.now(timezone.utc)
                    pub_text = (vr.get("publishedTimeText") or {}).get("simpleText") or ""
                    out.append(
                        {
                            "video_id": vid,
                            "published_at": pub,
                            "title": title,
                            "video_url": f"https://www.youtube.com/watch?v={vid}",
                            "description": "",
                            "published_text": pub_text,
                        }
                    )
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    return out[:15]


def _fetch_feed_entries(channel_id: str, *, channel_handle: str | None = None) -> list[dict]:
    last_exc: BaseException | None = None
    for cid in [channel_id]:
        if not cid:
            continue
        try:
            return _fetch_feed_entries_for_id(cid)
        except Exception as exc:
            last_exc = exc
            print(f"feed fetch failed channel_id={cid}: {type(exc).__name__}: {exc}")
    if channel_handle:
        resolved = _extract_channel_id_from_handle(channel_handle)
        if resolved and resolved != channel_id:
            channel_id = resolved
            try:
                return _fetch_feed_entries_for_id(resolved)
            except Exception as exc:
                last_exc = exc
                print(f"feed fetch failed resolved={resolved}: {type(exc).__name__}: {exc}")
    try:
        html_entries = _fetch_entries_from_channel_html(
            channel_id=channel_id or "", channel_handle=channel_handle
        )
        if html_entries:
            print(f"channel HTML fallback found {len(html_entries)} videos for {channel_id}")
            return html_entries
    except Exception as exc:
        last_exc = exc
        print(f"channel HTML fallback failed: {type(exc).__name__}: {exc}")
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("missing channel_id for feed fetch")


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
    channel_handle = source.get("channel_handle")
    channel_id = source.get("channel_id") or _extract_channel_id_from_handle(channel_handle)
    pk = f"USER#{user_id}"
    sk = f"FETCH#{issue_date}#{source_id}"
    now = datetime.now(timezone.utc).isoformat()

    if platform != "youtube":
        raise ValueError(f"Unsupported platform in ingest_source: {platform}")
    if not channel_id:
        raise ValueError(f"Missing channel_id/channel_handle for source={source_id}")

    window_start_utc, window_end_utc = _parse_issue_window(issue_date)

    try:
        entries = _fetch_feed_entries(channel_id, channel_handle=channel_handle)
    except Exception as exc:
        err = f"{type(exc).__name__}: {exc}"
        print(f"ingest_source feed fetch failed source={source_id} channel={channel_id}: {err}")
        table = boto3.resource("dynamodb").Table(TABLE_NAME)
        table.put_item(
            Item={
                "pk": pk,
                "sk": sk,
                "issue_date": issue_date,
                "source_id": source_id,
                "platform": platform,
                "channel_id": channel_id,
                "status": "FETCH_ERROR",
                "error": err[:500],
                "video_count": 0,
                "video_ids": [],
                "updated_at": now,
            }
        )
        return {"status": "FETCH_ERROR", "source_id": source_id, "video_count": 0, "error": err[:200]}

    # Latest video published on this issue_date (ET calendar day).
    in_window = [
        entry
        for entry in entries
        if window_start_utc <= entry["published_at"].astimezone(timezone.utc) < window_end_utc
    ]
    if in_window:
        latest = max(in_window, key=lambda e: e["published_at"])
        in_window = [latest]
    elif entries:
        # RSS timestamps missing — use newest listing only for this run.
        in_window = entries[:1]

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
