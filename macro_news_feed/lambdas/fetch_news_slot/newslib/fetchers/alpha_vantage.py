import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from ..config import AV_MACRO_TOPIC, MAX_ARTICLES_PER_SOURCE
from ..http_util import urlopen_json_request
from ..ids import article_id_from_url, canonicalize_url
from ..window import av_time_format, published_in_window

AV_BASE = "https://www.alphavantage.co/query"

# AV has no view-count field. sort=RELEVANCE is their "most relevant to markets" ordering.
AV_FETCH_LIMIT = 50
AV_SORT = "RELEVANCE"


def fetch_alpha_vantage(
    api_key: str,
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[dict], str | None]:
    if not api_key:
        return [], "missing ALPHA_VANTAGE_API_KEY"

    # One broad macro topic (AV ANDs multiple topics/tickers if you pass several).
    data, err = _request_feed(
        api_key,
        topic=AV_MACRO_TOPIC,
        time_from=av_time_format(window_start),
        time_to=av_time_format(window_end),
    )
    if err and not data.get("feed"):
        return [], err

    if "Note" in data or "Information" in data:
        return [], data.get("Note") or data.get("Information")

    out: list[dict] = []
    for article in data.get("feed") or []:
        if len(out) >= MAX_ARTICLES_PER_SOURCE:
            break
        published_at = _av_time_to_iso(article.get("time_published", ""))
        if not published_in_window(published_at, window_start, window_end):
            continue
        url_val = article.get("url", "")
        if not url_val:
            continue
        out.append(
            {
                "article_id": article_id_from_url(url_val),
                "provider": "alpha_vantage",
                "canonical_url": canonicalize_url(url_val),
                "url": url_val,
                "title": article.get("title", ""),
                "summary": (article.get("summary") or "")[:800],
                "published_at": published_at,
                "published": published_at,
                "source_name": article.get("source", ""),
                "sentiment_score": _float(article.get("overall_sentiment_score")),
                "sentiment_label": article.get("overall_sentiment_label", ""),
                "topics": [t.get("topic") for t in article.get("topics", []) if t.get("topic")],
                "tickers": [
                    ts.get("ticker")
                    for ts in (article.get("ticker_sentiment") or [])[:8]
                    if ts.get("ticker")
                ],
                "av_sort": AV_SORT,
            }
        )

    return out, None


def _request_feed(
    api_key: str,
    *,
    topic: str,
    time_from: str,
    time_to: str,
) -> tuple[dict, str | None]:
    params: dict[str, str] = {
        "function": "NEWS_SENTIMENT",
        "topics": topic,
        "time_from": time_from,
        "time_to": time_to,
        "limit": str(AV_FETCH_LIMIT),
        "sort": AV_SORT,
        "apikey": api_key,
    }
    url = f"{AV_BASE}?{urlencode(params)}"
    try:
        with urlopen_json_request(
            Request(url, headers={"User-Agent": "macro-news-feed/1.0"}), timeout=45
        ) as resp:
            data = json.loads(resp.read().decode())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        return {}, f"{type(exc).__name__}: {exc}"

    if "Note" in data or "Information" in data:
        return data, data.get("Note") or data.get("Information")
    return data, None


def _float(v) -> float | None:
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _av_time_to_iso(raw: str) -> str:
    raw = (raw or "").strip()
    if len(raw) < 15:
        return raw
    try:
        dt = datetime.strptime(raw[:15], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except ValueError:
        return raw
