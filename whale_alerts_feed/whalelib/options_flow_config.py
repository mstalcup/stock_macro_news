"""Config for anonymous large-options flow monitor."""
from __future__ import annotations

# Minimum estimated premium (USD) for a contract-day to count as "large flow"
MIN_CONTRACT_DAY_PREMIUM_USD = 1_000_000

# Keep only top N contracts per underlying per day (reduce SPY noise)
MAX_CONTRACTS_PER_UNDERLYING = 12

# Minimum single trade premium when Polygon trade tape is available
MIN_SINGLE_TRADE_PREMIUM_USD = 500_000

# Max underlyings per daily poll (S&P 500 cap + ETFs)
DEFAULT_WATCHLIST_LIMIT = 500

# Option expiries within this many days
MAX_DAYS_TO_EXPIRY = 120

# Ledger: close position when cumulative opposite flow exceeds this fraction of open est
CLOSE_FRACTION_OPPOSITE_FLOW = 0.5

# Extra ETFs always included
CORE_ETF_TICKERS = (
    "SPY", "QQQ", "IWM", "DIA", "SMH", "SOXX", "XLK", "XLF", "XLE", "XLV", "XLI", "XLY",
)

POLYGON_API_KEY_ENV = "POLYGON_API_KEY"
OPENAI_API_KEY_ENV = "OPENAI_API_KEY"
