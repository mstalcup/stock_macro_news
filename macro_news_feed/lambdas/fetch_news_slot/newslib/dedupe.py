from __future__ import annotations

from .config import MAX_DEDUPED_ARTICLES
from .ids import article_id_from_url, canonicalize_url, normalize_title


def _parse_published(article: dict) -> str:
    return article.get("published_at") or article.get("published") or ""


def dedupe_articles(by_provider: dict[str, list[dict]]) -> tuple[list[dict], dict]:
    """
    Merge per-provider normalized articles. Match on canonical_url, else similar title.
    Returns (deduped_list, stats).
    """
    merged: dict[str, dict] = {}
    url_index: dict[str, str] = {}
    title_index: dict[str, str] = {}
    multi_source = 0

    provider_order = ["alpha_vantage", "newsapi", "finnhub"]

    for provider in provider_order:
        for art in by_provider.get(provider, []):
            url = art.get("url", "")
            canon = art.get("canonical_url") or canonicalize_url(url)
            canon_id = article_id_from_url(canon or url)
            title_key = normalize_title(art.get("title", ""))

            target_id = canon_id
            if canon_id in url_index:
                target_id = url_index[canon_id]
            elif title_key and title_key in title_index:
                target_id = title_index[title_key]

            if target_id not in merged:
                merged[target_id] = {
                    "article_id": target_id,
                    "canonical_url": canon,
                    "title": art.get("title", ""),
                    "summary": art.get("summary", ""),
                    "published_at": _parse_published(art),
                    "source_name": art.get("source_name", ""),
                    "providers": [],
                    "provider_articles": {},
                    "tickers": [],
                    "topics": [],
                    "sentiment_score": None,
                    "sentiment_label": None,
                }
                if title_key:
                    title_index[title_key] = target_id
                if canon_id:
                    url_index[canon_id] = target_id

            row = merged[target_id]
            if provider not in row["providers"]:
                row["providers"].append(provider)
            row["provider_articles"][provider] = art

            if len(_parse_published(art)) > len(_parse_published(row)):
                row["published_at"] = _parse_published(art)
            if not row["summary"] and art.get("summary"):
                row["summary"] = art["summary"]
            if not row["source_name"] and art.get("source_name"):
                row["source_name"] = art["source_name"]

            for t in art.get("tickers") or []:
                if t and t not in row["tickers"]:
                    row["tickers"].append(t)
            for t in art.get("topics") or []:
                if t and t not in row["topics"]:
                    row["topics"].append(t)

            ss = art.get("sentiment_score")
            if ss is not None and (row["sentiment_score"] is None or abs(ss) > abs(row["sentiment_score"] or 0)):
                row["sentiment_score"] = ss
                row["sentiment_label"] = art.get("sentiment_label")

    deduped = list(merged.values())
    for row in deduped:
        if len(row["providers"]) > 1:
            multi_source += 1

    deduped.sort(key=lambda a: _parse_published(a), reverse=True)
    deduped = deduped[:MAX_DEDUPED_ARTICLES]

    stats = {
        "input_count": sum(len(v) for v in by_provider.values()),
        "deduped_count": len(deduped),
        "multi_source_count": multi_source,
    }
    return deduped, stats
