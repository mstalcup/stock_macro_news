"""News source configuration."""

MAX_ARTICLES_PER_SOURCE = 10
MAX_DEDUPED_ARTICLES = 30

# Active ingest sources. AV disabled by default — its NEWS_SENTIMENT feed is
# overwhelmingly single-name equity stories, not macro headline wires.
# Re-enable when we add a per-ticker or sentiment-scoring lane.
ENABLED_PROVIDERS = ["newsapi", "finnhub"]

# Re-use by_source/{provider}.json from S3 when it already has articles for this date/slot.
USE_S3_SOURCE_CACHE = True

# Alpha Vantage (optional): ticker-tagged sentiment wire, not broad macro topics.
AV_MACRO_TOPIC = "financial_markets"

NEWSAPI_QUERIES = [
    "stock market",
    "Federal Reserve interest rates",
    "inflation CPI",
    "S&P 500 earnings",
    "oil prices",
    "China economy trade",
]

FINNHUB_CATEGORIES = ["general", "forex", "crypto", "merger"]

SLOT_LABELS = {
    "pre_open": "Pre-market open (6:25 AM PT)",
    "pre_close": "Pre-market close (12:50 PM PT)",
}
