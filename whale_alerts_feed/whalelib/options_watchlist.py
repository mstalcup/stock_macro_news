"""Build/load options flow watchlist (S&P 500 + core ETFs)."""
from __future__ import annotations

import json
from pathlib import Path
from urllib.request import Request, urlopen

from .backtest_config import EDGAR_USER_AGENT
from .options_flow_config import CORE_ETF_TICKERS, DEFAULT_WATCHLIST_LIMIT
from .prices import normalize_symbol

SP500_CSV = (
    "https://raw.githubusercontent.com/datasets/s-and-p-500-companies/master/data/constituents.csv"
)


def _fetch_sp500_tickers() -> list[str]:
    req = Request(SP500_CSV, headers={"User-Agent": EDGAR_USER_AGENT})
    try:
        with urlopen(req, timeout=30) as resp:
            text = resp.read().decode("utf-8", errors="replace")
    except OSError:
        return []
    out: list[str] = []
    for line in text.splitlines()[1:]:
        if not line.strip():
            continue
        sym = line.split(",")[0].strip().strip('"')
        t = normalize_symbol(sym)
        if t and t not in out:
            out.append(t)
    return out


def build_watchlist(*, limit: int = DEFAULT_WATCHLIST_LIMIT) -> list[str]:
    tickers = list(CORE_ETF_TICKERS)
    for t in _fetch_sp500_tickers():
        if t not in tickers:
            tickers.append(t)
        if len(tickers) >= limit:
            break
    return tickers[:limit]


def load_watchlist(path: Path) -> list[str]:
    if not path.is_file():
        return build_watchlist()
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return [normalize_symbol(t) or t for t in data if t]
    if isinstance(data, dict):
        return [normalize_symbol(t) or t for t in data.get("tickers") or [] if t]
    return build_watchlist()
