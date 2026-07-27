import json
from datetime import datetime, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from ..config import FINNHUB_CATEGORIES, MAX_ARTICLES_PER_SOURCE
from ..http_util import urlopen_json_request
from ..ids import article_id_from_url, canonicalize_url
from ..window import published_in_window

FINNHUB_NEWS = "https://finnhub.io/api/v1/news"


def fetch_finnhub(
    api_key: str,
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[dict], str | None]:
    if not api_key:
        return [], "missing FINNHUB_API_KEY"

    seen: set[str] = set()
    out: list[dict] = []
    per_cat = max(5, MAX_ARTICLES_PER_SOURCE // max(len(FINNHUB_CATEGORIES), 1))

    for category in FINNHUB_CATEGORIES:
        if len(out) >= MAX_ARTICLES_PER_SOURCE:
            break
        params = urlencode({"category": category, "token": api_key})
        url = f"{FINNHUB_NEWS}?{params}"
        try:
            with urlopen_json_request(
                Request(url, headers={"User-Agent": "macro-news-feed/1.0"}), timeout=25
            ) as resp:
                rows = json.loads(resp.read().decode())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError):
            continue

        if not isinstance(rows, list):
            continue

        for article in rows:
            if len(out) >= MAX_ARTICLES_PER_SOURCE:
                break
            url_val = article.get("url", "")
            if not url_val or url_val in seen:
                continue
            published_at = _finnhub_ts(article.get("datetime"))
            if not published_in_window(published_at, window_start, window_end):
                continue
            seen.add(url_val)
            out.append(
                {
                    "article_id": article_id_from_url(url_val),
                    "provider": "finnhub",
                    "canonical_url": canonicalize_url(url_val),
                    "url": url_val,
                    "title": article.get("headline", ""),
                    "summary": (article.get("summary") or "")[:800],
                    "published_at": published_at,
                    "published": published_at,
                    "source_name": article.get("source", ""),
                    "finnhub_category": category,
                    "related": article.get("related", ""),
                    "sentiment_score": None,
                    "sentiment_label": None,
                    "topics": [category],
                    "tickers": [article.get("related")] if article.get("related") else [],
                }
            )

    out.sort(key=lambda a: a.get("published_at", ""), reverse=True)
    return out[:MAX_ARTICLES_PER_SOURCE], None


def _finnhub_ts(ts) -> str:
    try:
        return datetime.fromtimestamp(int(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return ""
