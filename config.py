"""
shared/config.py — Constants and configuration shared across all Lambda functions.
"""

# ── Sector ETFs ──────────────────────────────────────────────────────────────
SECTOR_ETFS = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLE":  "Energy",
    "XLV":  "Health Care",
    "XLI":  "Industrials",
    "XLY":  "Consumer Discretionary",
    "XLP":  "Consumer Staples",
    "XLB":  "Materials",
    "XLC":  "Communication Services",
    "XLRE": "Real Estate",
    "XLU":  "Utilities",
}

# ── Macro / Thematic ETFs ────────────────────────────────────────────────────
MACRO_ETFS = {
    "GLD":  "Gold",
    "IBIT": "Bitcoin (BlackRock)",
    "TLT":  "Long-Term Bonds (20yr)",
    "SHY":  "Short-Term Bonds (1-3yr)",
    "USO":  "Crude Oil",
    "UUP":  "US Dollar Index",
    "SPY":  "S&P 500",
    "QQQ":  "Nasdaq 100",
    "IWM":  "Russell 2000 (Small Cap)",
    "VIX":  "Volatility Index",   # fetched separately via ^VIX
}

ALL_TICKERS = list(SECTOR_ETFS.keys()) + list(MACRO_ETFS.keys())

# VIX uses a different yfinance symbol
VIX_TICKER = "^VIX"

# ── Momentum weights ─────────────────────────────────────────────────────────
MOMENTUM_WEIGHTS = {
    "1d":  0.5,
    "5d":  0.3,
    "20d": 0.2,
}

# ── Rotation alert thresholds ────────────────────────────────────────────────
ROTATION_STD_THRESHOLD = 1.5   # std deviations above rolling mean to trigger alert
ROTATION_CLUSTER_MIN   = 2     # min sectors moving together to flag a cluster

# ── Related sector clusters (for cluster detection) ──────────────────────────
SECTOR_CLUSTERS = {
    "Risk-On":      ["XLK", "XLY", "XLC", "IWM"],
    "Defensive":    ["XLP", "XLU", "XLV", "GLD", "TLT"],
    "Cyclical":     ["XLI", "XLB", "XLE"],
    "Financial":    ["XLF", "XLRE"],
    "Commodities":  ["XLE", "XLB", "USO", "GLD"],
}

# ── Alpha Vantage news topics for macro context ──────────────────────────────
# See: https://www.alphavantage.co/documentation/#news-sentiment
AV_MACRO_TOPICS = [
    "economy_macro",
    "economy_monetary",
    "economy_fiscal",
    "finance",
    "financial_markets",
    "ipo",
    "mergers_and_acquisitions",
    "technology",
    "energy_transportation",
]

# ── NewsAPI queries ──────────────────────────────────────────────────────────
NEWSAPI_QUERIES = [
    "stock market",
    "Federal Reserve interest rates",
    "inflation CPI",
    "oil prices OPEC",
    "China economy trade",
    "semiconductor chips AI",
    "cryptocurrency bitcoin",
    "gold silver commodities",
]

# ── DynamoDB ─────────────────────────────────────────────────────────────────
DYNAMO_TABLE      = "market-pulse"
DYNAMO_TTL_DAYS   = 90

# Sort key values
SK_RAW_DATA   = "raw_data"
SK_SIGNALS    = "signals"
SK_NEWSLETTER = "newsletter"

# ── Discord ───────────────────────────────────────────────────────────────────
DISCORD_MAX_CHARS = 2000   # Discord message limit per post
DISCORD_EMBED_COLOR_GREEN  = 0x2ecc71
DISCORD_EMBED_COLOR_RED    = 0xe74c3c
DISCORD_EMBED_COLOR_YELLOW = 0xf39c12
DISCORD_EMBED_COLOR_BLUE   = 0x3498db
