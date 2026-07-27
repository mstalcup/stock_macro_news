"""Sector rotation v1 — 11 GICS Select Sector SPDRs vs SPY."""

from __future__ import annotations

# Display order (GICS)
SECTOR_ETFS: dict[str, str] = {
    "XLC": "Communication",
    "XLY": "Cons Disc",
    "XLP": "Cons Staples",
    "XLE": "Energy",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLK": "Technology",
    "XLU": "Utilities",
}

BENCHMARK = "SPY"

# Top N sectors for drill-down (by rotation score)
DRILL_IN_COUNT = 2
DRILL_HOLDINGS_PER_SECTOR = 12
DRILL_TOP_MOVERS = 5
DRILL_TOP_VOLUME = 5

# IN: RS ratio above 20d MA and 5d relative return positive
# OUT: RS below MA or 5d rel negative with weak momentum
IN_MIN_REL_5D = 0.0
OUT_MAX_REL_5D = -0.25
