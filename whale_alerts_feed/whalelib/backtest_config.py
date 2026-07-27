"""Backtest parameters — keep stable across reruns for comparable rankings."""

from __future__ import annotations

LOOKBACK_YEARS = 2
MIN_SIGNALS_TO_RANK = 8
ANTICIPATION_LOOKBACK_DAYS = 120
AUM_FLOOR_USD = 250_000_000

HORIZONS = (1, 5, 20, 60)

RANK_WEIGHTS = {
    "median_alpha_20d": 0.35,
    "median_alpha_60d": 0.25,
    "anticipation_link_rate": 0.20,
    "hit_rate_20d": 0.10,
    "signal_count_log": 0.10,
}

ALERT_CONFIDENCE_IMMEDIATE = 70
ALERT_COMBINED_URGENT = 80

EDGAR_USER_AGENT = "whale-alerts-feed admin@example.com"

# CIKs that file 13D on behalf of many issuers (law firms / agents) — not funds
FILING_AGENT_MIN_ISSUERS = 25

# Phase 2 signal layers
SIGNAL_LAYERS = ("schedule_13dg", "8k_stake", "volume_spike", "news_stake")
CONFLUENCE_WINDOW_DAYS = 14
VOLUME_SPIKE_MIN_RATIO = 2.0
VOLUME_LOOKBACK_DAYS = 20
NEWS_STAKE_KEYWORDS = (
    "activist",
    "stake",
    "13d",
    "13-d",
    "beneficial owner",
    "takes position",
    "builds position",
    "increases stake",
)
