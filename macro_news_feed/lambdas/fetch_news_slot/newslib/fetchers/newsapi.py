import json
from datetime import datetime
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from ..config import MAX_ARTICLES_PER_SOURCE, NEWSAPI_QUERIES
from ..http_util import urlopen_json_request
from ..ids import article_id_from_url, canonicalize_url
from ..window import published_in_window, window_iso_utc_z

NEWSAPI_TOP = "https://newsapi.org/v2/top-headlines"
NEWSAPI_EVERYTHING = "https://newsapi.org/v2/everything"

TOP_HEADLINE_BUCKETS = [
    {"country": "us", "category": "business"},
    {"country": "us", "category": "general"},
]


def fetch_newsapi(
    api_key: str,
    *,
    window_start: datetime,
    window_end: datetime,
) -> tuple[list[dict], str | None]:
    if not api_key:
        return [], "missing NEWS_API_KEY"

    seen: set[str] = set()
    out: list[dict] = []
    last_err: str | None = None
    per_bucket = max(4, MAX_ARTICLES_PER_SOURCE // len(TOP_HEADLINE_BUCKETS))

    for bucket in TOP_HEADLINE_BUCKETS:
        if len(out) >= MAX_ARTICLES_PER_SOURCE:
            break
        params = {**bucket, "pageSize": str(per_bucket), "apiKey": api_key}
        url = f"{NEWSAPI_TOP}?{urlencode(params)}"
        try:
            with urlopen_json_request(
                Request(url, headers={"User-Agent": "macro-news-feed/1.0"}), timeout=25
            ) as resp:
                data = json.loads(resp.read().decode())
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            continue

        if data.get("status") != "ok":
            last_err = data.get("message") or data.get("code") or "newsapi error"
            continue

        tag = f"top-headlines:{bucket.get('category', 'news')}"
        _append_articles(
            data.get("articles", []),
            out,
            seen,
            query_tag=tag,
            window_start=window_start,
            window_end=window_end,
        )

    if len(out) < MAX_ARTICLES_PER_SOURCE:
        for query in NEWSAPI_QUERIES[:4]:
            if len(out) >= MAX_ARTICLES_PER_SOURCE:
                break
            from_iso, to_iso = window_iso_utc_z(window_start, window_end)
            params = {
                "q": query,
                "from": from_iso,
                "to": to_iso,
                "sortBy": "publishedAt",
                "language": "en",
                "pageSize": "8",
                "apiKey": api_key,
            }
            url = f"{NEWSAPI_EVERYTHING}?{urlencode(params)}"
            try:
                with urlopen_json_request(
                    Request(url, headers={"User-Agent": "macro-news-feed/1.0"}), timeout=25
                ) as resp:
                    data = json.loads(resp.read().decode())
            except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_err = f"{type(exc).__name__}: {exc}"
                continue

            if data.get("status") != "ok":
                last_err = data.get("message") or "newsapi everything error"
                continue

            _append_articles(
                data.get("articles", []),
                out,
                seen,
                query_tag=f"everything:{query}",
                window_start=window_start,
                window_end=window_end,
            )

    out.sort(key=lambda a: a.get("published_at", ""), reverse=True)
    result = out[:MAX_ARTICLES_PER_SOURCE]
    return result, (last_err if not result else None)


def _append_articles(
    articles: list,
    out: list[dict],
    seen: set[str],
    *,
    query_tag: str,
    window_start: datetime,
    window_end: datetime,
) -> None:
    for article in articles:
        if len(out) >= MAX_ARTICLES_PER_SOURCE:
            return
        url_val = article.get("url", "")
        if not url_val or url_val in seen or "[Removed]" in (article.get("title") or ""):
            continue
        published_at = article.get("publishedAt", "")
        if not published_in_window(published_at, window_start, window_end):
            continue
        seen.add(url_val)
        canon = canonicalize_url(url_val)
        out.append(
            {
                "article_id": article_id_from_url(url_val),
                "provider": "newsapi",
                "canonical_url": canon,
                "url": url_val,
                "title": article.get("title", ""),
                "summary": (article.get("description") or "")[:800],
                "published_at": published_at,
                "published": published_at,
                "source_name": (article.get("source") or {}).get("name", ""),
                "query_tag": query_tag,
                "sentiment_score": None,
                "sentiment_label": None,
                "topics": [],
                "tickers": [],
            }
        )
