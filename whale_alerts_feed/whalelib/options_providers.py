"""Options flow data providers — Polygon (trades) or Yahoo chain (daily fallback)."""
from __future__ import annotations

import json
import logging
import math
import os
import time
from datetime import date, timedelta
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .backtest_config import EDGAR_USER_AGENT
from .options_flow_config import (
    MAX_CONTRACTS_PER_UNDERLYING,
    MAX_DAYS_TO_EXPIRY,
    MIN_CONTRACT_DAY_PREMIUM_USD,
    MIN_SINGLE_TRADE_PREMIUM_USD,
    POLYGON_API_KEY_ENV,
)
from .prices import normalize_symbol

LOG = logging.getLogger(__name__)
POLYGON_BASE = "https://api.polygon.io"
YAHOO_OPTIONS = "https://query2.finance.yahoo.com/v7/finance/options"


def polygon_api_key() -> str:
    return (os.environ.get(POLYGON_API_KEY_ENV) or "").strip()


def _get_json(url: str, *, params: dict | None = None) -> dict | list | None:
    q = f"?{urlencode(params)}" if params else ""
    req = Request(f"{url}{q}", headers={"User-Agent": EDGAR_USER_AGENT})
    try:
        with urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (HTTPError, OSError, json.JSONDecodeError, TimeoutError) as exc:
        LOG.debug("fetch failed %s: %r", url, exc)
        return None


def _yf_option_contracts(ticker: str) -> list[dict]:
    """yfinance chain — works where raw Yahoo options API returns 401."""
    try:
        import yfinance as yf
    except ImportError:
        return []
    sym = normalize_symbol(ticker) or ticker
    try:
        tkr = yf.Ticker(sym)
        expiries = list(tkr.options or [])[:6]
    except Exception:
        return []
    cutoff = (date.today() + timedelta(days=MAX_DAYS_TO_EXPIRY)).isoformat()
    quotes: list[dict] = []
    for exp in expiries:
        if exp > cutoff:
            continue
        try:
            chain = tkr.option_chain(exp)
        except Exception:
            continue
        for frame, typ in ((chain.calls, "call"), (chain.puts, "put")):
            if frame is None or frame.empty:
                continue
            for _, row in frame.iterrows():
                vol_raw = row.get("volume")
                if vol_raw is None or (isinstance(vol_raw, float) and math.isnan(vol_raw)):
                    continue
                vol = int(vol_raw)
                if vol <= 0:
                    continue
                last_raw = row.get("lastPrice") if row.get("lastPrice") == row.get("lastPrice") else row.get("ask")
                if last_raw is None or (isinstance(last_raw, float) and math.isnan(last_raw)):
                    continue
                last = float(last_raw)
                if last <= 0:
                    continue
                premium = vol * last * 100
                if premium < MIN_CONTRACT_DAY_PREMIUM_USD:
                    continue
                oi_raw = row.get("openInterest")
                oi = 0 if oi_raw is None or (isinstance(oi_raw, float) and math.isnan(oi_raw)) else int(oi_raw)
                quotes.append(
                    {
                        "underlying": sym,
                        "option_type": typ,
                        "strike": float(row.get("strike") or 0),
                        "expiry": exp,
                        "volume": vol,
                        "open_interest": oi,
                        "last_price": last,
                        "est_premium_usd": round(premium, 2),
                        "contract_symbol": str(row.get("contractSymbol") or ""),
                        "source": "yfinance_chain",
                    }
                )
    return quotes


def yahoo_option_contracts(ticker: str) -> list[dict]:
    """Near-term chain with volume/OI — no individual block prints."""
    rows = _yf_option_contracts(ticker)
    if rows:
        return rows
    sym = normalize_symbol(ticker) or ticker
    data = _get_json(f"{YAHOO_OPTIONS}/{sym}")
    if not data or not isinstance(data, dict):
        return []
    result = ((data.get("optionChain") or {}).get("result") or [])
    if not result:
        return []
    root = result[0]
    quotes = []
    cutoff = (date.today() + timedelta(days=MAX_DAYS_TO_EXPIRY)).isoformat()
    for block in root.get("options") or []:
        exp = (block.get("expirationDate") or "")[:10]
        if exp and exp > cutoff:
            continue
        for side, typ in (("calls", "call"), ("puts", "put")):
            for c in block.get(side) or []:
                vol = int(c.get("volume") or 0)
                oi = int(c.get("openInterest") or 0)
                last = float(c.get("lastPrice") or c.get("ask") or 0)
                strike = float(c.get("strike") or 0)
                if vol <= 0 or last <= 0:
                    continue
                premium = vol * last * 100
                if premium < MIN_CONTRACT_DAY_PREMIUM_USD:
                    continue
                quotes.append(
                    {
                        "underlying": sym,
                        "option_type": typ,
                        "strike": strike,
                        "expiry": exp,
                        "volume": vol,
                        "open_interest": oi,
                        "last_price": last,
                        "est_premium_usd": round(premium, 2),
                        "contract_symbol": c.get("contractSymbol") or "",
                        "source": "yahoo_chain",
                    }
                )
    return quotes


def polygon_option_snapshot(underlying: str, *, api_key: str = "") -> list[dict]:
    """One API call per underlying — day volume on all contracts."""
    key = api_key or polygon_api_key()
    if not key:
        return []
    sym = normalize_symbol(underlying) or underlying
    data = _get_json(
        f"{POLYGON_BASE}/v3/snapshot/options/{sym}",
        params={"apiKey": key},
    )
    if not data or not isinstance(data, dict):
        return []
    out: list[dict] = []
    for row in data.get("results") or []:
        details = row.get("details") or {}
        day = row.get("day") or {}
        last_trade = row.get("last_trade") or {}
        vol = int(day.get("volume") or 0)
        vwap = float(day.get("vwap") or day.get("close") or 0)
        last_p = float(last_trade.get("price") or vwap or 0)
        price = vwap or last_p
        if vol <= 0 or price <= 0:
            continue
        premium = vol * price * 100
        if premium < MIN_CONTRACT_DAY_PREMIUM_USD:
            continue
        ctype = (details.get("contract_type") or "").lower()
        out.append(
            {
                "underlying": sym,
                "option_type": "put" if ctype == "put" else "call",
                "strike": float(details.get("strike_price") or 0),
                "expiry": (details.get("expiration_date") or "")[:10],
                "volume": vol,
                "open_interest": int((row.get("open_interest") or 0)),
                "last_price": price,
                "est_premium_usd": round(premium, 2),
                "contract_symbol": details.get("ticker") or "",
                "source": "polygon_snapshot",
                "last_trade_size": int(last_trade.get("size") or 0),
                "last_trade_premium_usd": round(
                    int(last_trade.get("size") or 0) * float(last_trade.get("price") or 0) * 100, 2
                ),
            }
        )
    return out


def polygon_large_trades(
    option_symbol: str,
    *,
    trade_date: str,
    api_key: str = "",
    min_premium: float = MIN_SINGLE_TRADE_PREMIUM_USD,
) -> list[dict]:
    """Individual prints for one contract on a date (Polygon paid plan)."""
    key = api_key or polygon_api_key()
    if not key:
        return []
    sym = option_symbol if option_symbol.startswith("O:") else f"O:{option_symbol}"
    data = _get_json(
        f"{POLYGON_BASE}/v3/trades/{sym}",
        params={
            "timestamp": trade_date,
            "limit": 5000,
            "order": "desc",
            "apiKey": key,
        },
    )
    if not data or not isinstance(data, dict):
        return []
    out = []
    for t in data.get("results") or []:
        size = int(t.get("size") or 0)
        price = float(t.get("price") or 0)
        prem = size * price * 100
        if prem < min_premium:
            continue
        out.append(
            {
                "trade_ts": t.get("sip_timestamp") or t.get("participant_timestamp") or "",
                "size": size,
                "price": price,
                "premium_usd": round(prem, 2),
                "conditions": t.get("conditions") or [],
                "exchange": t.get("exchange") or "",
            }
        )
    return out


def scan_underlying(
    ticker: str,
    *,
    trade_date: str | None = None,
    fetch_block_trades: bool = False,
) -> list[dict]:
    """Return large-flow contract rows for one underlying."""
    td = trade_date or date.today().isoformat()
    key = polygon_api_key()
    rows: list[dict] = []
    if key:
        rows = polygon_option_snapshot(ticker, api_key=key)
        time.sleep(0.15)  # gentle rate limit
    else:
        rows = yahoo_option_contracts(ticker)
        time.sleep(0.8)  # yfinance: be gentle

    events = []
    for r in rows:
        ev = {
            **r,
            "trade_date": td,
            "signal_type": "options_large_flow",
            "direction_hint": "bearish" if r["option_type"] == "put" else "bullish",
        }
        if fetch_block_trades and key and r.get("contract_symbol"):
            if (r.get("last_trade_premium_usd") or 0) >= MIN_SINGLE_TRADE_PREMIUM_USD:
                ev["block_trades"] = polygon_large_trades(r["contract_symbol"], trade_date=td, api_key=key)
                ev["signal_type"] = "options_block_print"
            time.sleep(0.12)
        events.append(ev)
    events.sort(key=lambda x: float(x.get("est_premium_usd") or 0), reverse=True)
    return events[:MAX_CONTRACTS_PER_UNDERLYING]
