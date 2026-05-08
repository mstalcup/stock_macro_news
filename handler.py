"""
lambdas/fetch_market_data/handler.py

Lambda 1 of 4 in the Market Pulse Step Function.

Fetches:
  - ETF price data (yfinance) → 1d, 5d, 20d returns for all sector + macro ETFs
  - News sentiment (Alpha Vantage NEWS_SENTIMENT endpoint) → top articles with scores
  - Macro headlines (NewsAPI) → top headlines across our query set
  - VIX level (yfinance ^VIX)

Stores raw JSON payload → DynamoDB SK=raw_data
"""
import os
import json
import requests
from datetime import datetime, timezone, timedelta

import yfinance as yf

# When running in Lambda, shared/ is a layer or bundled alongside.
# For local dev the project root is on sys.path.
try:
    from shared.config import (
        SECTOR_ETFS, MACRO_ETFS, ALL_TICKERS, VIX_TICKER,
        AV_MACRO_TOPICS, NEWSAPI_QUERIES, SK_RAW_DATA,
    )
    from shared.dynamo import save_report
except ImportError:
    import sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
    from shared.config import (
        SECTOR_ETFS, MACRO_ETFS, ALL_TICKERS, VIX_TICKER,
        AV_MACRO_TOPICS, NEWSAPI_QUERIES, SK_RAW_DATA,
    )
    from shared.dynamo import save_report


# ─────────────────────────────────────────────────────────────────────────────
# 1. ETF Prices via yfinance
# ─────────────────────────────────────────────────────────────────────────────

def fetch_etf_data() -> dict:
    """
    Download 22 trading days of daily OHLCV for all ETFs.
    Returns a dict keyed by ticker with price history and computed returns.

    Inspired by TradingAgents' y_finance.py pattern: download bulk, then
    slice per-ticker rather than making N individual calls.
    """
    print("[yfinance] Downloading ETF price history...")

    # All sector + macro tickers in one bulk call (much faster than per-ticker)
    tickers_to_fetch = ALL_TICKERS.copy()
    # VIX is separate — different symbol format
    data = yf.download(
        tickers_to_fetch,
        period="30d",        # 30 calendar days ≈ 22 trading days
        interval="1d",
        group_by="ticker",
        auto_adjust=True,
        progress=False,
    )

    # Also fetch VIX
    vix_data = yf.download(VIX_TICKER, period="5d", interval="1d",
                           auto_adjust=True, progress=False)

    results = {}

    for ticker in tickers_to_fetch:
        try:
            if len(tickers_to_fetch) == 1:
                closes = data["Close"].dropna()
            else:
                closes = data["Close"][ticker].dropna()

            if len(closes) < 5:
                print(f"  [WARN] Not enough data for {ticker}, skipping")
                continue

            current_price = float(closes.iloc[-1])

            def pct_return(n_days: int) -> float | None:
                if len(closes) <= n_days:
                    return None
                old = float(closes.iloc[-(n_days + 1)])
                return round((current_price - old) / old * 100, 4) if old else None

            results[ticker] = {
                "ticker":        ticker,
                "name":          SECTOR_ETFS.get(ticker) or MACRO_ETFS.get(ticker, ticker),
                "price":         round(current_price, 4),
                "return_1d":     pct_return(1),
                "return_5d":     pct_return(5),
                "return_20d":    pct_return(20),
                "prices_20d":    [round(float(p), 4) for p in closes.iloc[-20:].tolist()],
            }

        except Exception as e:
            print(f"  [WARN] Failed to parse {ticker}: {e}")

    # VIX
    try:
        vix_close = float(vix_data["Close"].dropna().iloc[-1])
        results["VIX"] = {"ticker": "VIX", "name": "Volatility Index",
                          "price": round(vix_close, 2)}
    except Exception as e:
        print(f"  [WARN] VIX fetch failed: {e}")

    print(f"[yfinance] Fetched {len(results)} tickers")
    return results


# ─────────────────────────────────────────────────────────────────────────────
# 2. News Sentiment via Alpha Vantage
#    Docs: https://www.alphavantage.co/documentation/#news-sentiment
#    Key learnings from TradingAgents' alpha_vantage_news.py:
#      - Use NEWS_SENTIMENT function with topic filters
#      - Each article has overall_sentiment_score + per-ticker relevance scores
#      - Sort by relevance_score DESC to get most market-moving stories
# ─────────────────────────────────────────────────────────────────────────────

AV_BASE_URL = "https://www.alphavantage.co/query"

def fetch_av_news(topics: list[str] | None = None, limit: int = 50) -> list[dict]:
    """
    Fetch news + sentiment from Alpha Vantage.
    Returns a list of article dicts with sentiment scores.

    Alpha Vantage free tier: 25 req/day, so we make ONE broad call with
    the most important topics joined, rather than per-topic calls.
    """
    api_key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
    if not api_key:
        print("[AlphaVantage] No API key — skipping news sentiment")
        return []

    # Use top macro topics; join with comma for multi-topic filter
    topic_filter = ",".join((topics or AV_MACRO_TOPICS)[:5])  # AV allows up to 5

    # Time window: last 48 hours in AV format: YYYYMMDDTHHMM
    time_from = (datetime.now(timezone.utc) - timedelta(hours=48)).strftime("%Y%m%dT%H%M")

    params = {
        "function":   "NEWS_SENTIMENT",
        "topics":     topic_filter,
        "time_from":  time_from,
        "limit":      limit,
        "sort":       "RELEVANCE",
        "apikey":     api_key,
    }

    try:
        resp = requests.get(AV_BASE_URL, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()

        if "feed" not in data:
            print(f"[AlphaVantage] Unexpected response: {list(data.keys())}")
            return []

        articles = []
        for article in data["feed"]:
            # Normalise each article to a compact form
            articles.append({
                "title":              article.get("title", ""),
                "url":                article.get("url", ""),
                "published":          article.get("time_published", ""),
                "source":             article.get("source", ""),
                "summary":            article.get("summary", "")[:500],   # truncate
                "overall_sentiment":  article.get("overall_sentiment_label", ""),
                "sentiment_score":    float(article.get("overall_sentiment_score", 0)),
                "topics":             [t["topic"] for t in article.get("topics", [])],
                # Per-ticker sentiment (useful for our rotation analysis later)
                "ticker_sentiment":   [
                    {
                        "ticker":           ts.get("ticker"),
                        "relevance_score":  float(ts.get("relevance_score", 0)),
                        "sentiment_label":  ts.get("ticker_sentiment_label", ""),
                        "sentiment_score":  float(ts.get("ticker_sentiment_score", 0)),
                    }
                    for ts in article.get("ticker_sentiment", [])[:5]   # top 5 tickers
                ],
            })

        print(f"[AlphaVantage] Fetched {len(articles)} news articles")
        return articles

    except Exception as e:
        print(f"[AlphaVantage] Error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# 3. Macro Headlines via NewsAPI
#    Free tier: 100 req/day, results limited to 1 month old
# ─────────────────────────────────────────────────────────────────────────────

NEWSAPI_BASE_URL = "https://newsapi.org/v2/everything"

def fetch_newsapi_headlines(queries: list[str] | None = None) -> list[dict]:
    """
    Fetch macro headlines from NewsAPI.
    We make one call per query topic and deduplicate by URL.
    Returns top articles sorted by publishedAt DESC.
    """
    api_key = os.environ.get("NEWS_API_KEY", "")
    if not api_key:
        print("[NewsAPI] No API key — skipping macro headlines")
        return []

    seen_urls = set()
    all_articles = []
    from_date = (datetime.now(timezone.utc) - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")

    for query in (queries or NEWSAPI_QUERIES):
        try:
            resp = requests.get(
                NEWSAPI_BASE_URL,
                params={
                    "q":          query,
                    "from":       from_date,
                    "sortBy":     "relevancy",
                    "language":   "en",
                    "pageSize":   5,       # 5 per query × 8 queries = up to 40 articles
                    "apiKey":     api_key,
                },
                timeout=20,
            )
            resp.raise_for_status()
            data = resp.json()

            for article in data.get("articles", []):
                url = article.get("url", "")
                if url in seen_urls or "[Removed]" in article.get("title", ""):
                    continue
                seen_urls.add(url)
                all_articles.append({
                    "title":       article.get("title", ""),
                    "url":         url,
                    "published":   article.get("publishedAt", ""),
                    "source":      article.get("source", {}).get("name", ""),
                    "description": (article.get("description") or "")[:400],
                    "query_tag":   query,   # which topic triggered this result
                })

        except Exception as e:
            print(f"[NewsAPI] Error on query '{query}': {e}")

    # Sort newest first
    all_articles.sort(key=lambda a: a.get("published", ""), reverse=True)
    print(f"[NewsAPI] Fetched {len(all_articles)} unique headlines")
    return all_articles


# ─────────────────────────────────────────────────────────────────────────────
# Lambda handler
# ─────────────────────────────────────────────────────────────────────────────

def handler(event: dict, context) -> dict:
    """
    Step Functions calls this as the first step.
    event may contain:
      - date: 'YYYY-MM-DD' override (default: today)
    """
    date_str = event.get("date") or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"[fetch_market_data] Running for date: {date_str}")

    # 1. ETF prices
    etf_data = fetch_etf_data()

    # 2. News sentiment (Alpha Vantage)
    av_news = fetch_av_news()

    # 3. Macro headlines (NewsAPI)
    newsapi_headlines = fetch_newsapi_headlines()

    # Assemble raw payload
    raw_payload = {
        "date":               date_str,
        "generated_at":       datetime.now(timezone.utc).isoformat(),
        "etf_data":           etf_data,
        "av_news":            av_news,
        "newsapi_headlines":  newsapi_headlines,
    }

    # Store to DynamoDB
    save_report(date_str, SK_RAW_DATA, raw_payload)

    # Pass date forward to next Step Functions state
    return {"date": date_str, "status": "ok", "etf_count": len(etf_data)}
