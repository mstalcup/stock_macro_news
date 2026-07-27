from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote as url_quote
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
LOG = logging.getLogger(__name__)

# Map panel tickers to Yahoo chart symbols (same idea as influencer_feed/tools/score_quick_returns.py)
YAHOO_ALIASES: dict[str, str] = {
    "BTC": "BTC-USD",
    "BITCOIN": "BTC-USD",
    "ETH": "ETH-USD",
    "USDJPY": "JPY=X",
    "GBPUSD": "GBPUSD=X",
    "EURUSD": "EURUSD=X",
}


def normalize_symbol(ticker: str) -> str | None:
    t = (ticker or "").strip().upper().replace("$", "")
    if not t:
        return None
    if t in YAHOO_ALIASES:
        return YAHOO_ALIASES[t]
    if "-" in t and t.endswith("-USD"):
        return t
    if t.endswith("=X"):
        return t
    return t


def calendar_add(date_str: str, days: int) -> str:
    d = datetime.fromisoformat(date_str).date()
    return (d + timedelta(days=days)).isoformat()


def _yahoo_chart(
    *,
    yahoo_symbol: str,
    period1: int,
    period2: int,
) -> tuple[list[int], list[float]] | None:
    sym = url_quote(yahoo_symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        f"?period1={period1}&period2={period2}&interval=1d"
    )
    req = Request(url, headers={"User-Agent": "llm-sentiment-feed/1.0"}, method="GET")
    try:
        with urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        LOG.warning("yahoo chart failed %s: %r", yahoo_symbol, exc)
        return None
    res = (data.get("chart") or {}).get("result")
    if not res:
        LOG.warning("yahoo chart empty result %s", yahoo_symbol)
        return None
    r0 = res[0]
    ts = r0.get("timestamp") or []
    quotes = (r0.get("indicators") or {}).get("quote") or [{}]
    closes = (quotes[0].get("close") if quotes else None) or []
    out_ts: list[int] = []
    out_c: list[float] = []
    for i, t in enumerate(ts):
        if i >= len(closes):
            break
        c = closes[i]
        if c is None:
            continue
        out_ts.append(int(t))
        out_c.append(float(c))
    if not out_ts:
        return None
    return out_ts, out_c


def fetch_yahoo_daily_close(*, symbol: str, trade_date: str) -> float | None:
    """US calendar-day close from Yahoo daily bars (Eastern session date)."""
    ysym = normalize_symbol(symbol)
    if not ysym:
        return None
    try:
        d = date.fromisoformat(trade_date)
    except ValueError:
        return None
    start = datetime.combine(d - timedelta(days=7), datetime.min.time(), tzinfo=ET)
    end = datetime.combine(d + timedelta(days=3), datetime.min.time(), tzinfo=ET)
    chart = _yahoo_chart(
        yahoo_symbol=ysym,
        period1=int(start.timestamp()),
        period2=int(end.timestamp()),
    )
    if not chart:
        return None
    ts_list, closes = chart
    for i, ts in enumerate(ts_list):
        bar_d = datetime.fromtimestamp(ts, tz=ET).date()
        if bar_d == d:
            return closes[i]
    LOG.warning("yahoo no bar for %s on %s", ysym, trade_date)
    return None


def fetch_finnhub_daily_close(*, finnhub_key: str, symbol: str, trade_date: str) -> float | None:
    """
    Finnhub /stock/candle — requires paid tier; kept as optional fallback.
    Free keys return 403 / s=no_data (macro feed only uses /news on free).
    """
    if not finnhub_key:
        return None
    ysym = normalize_symbol(symbol)
    if not ysym:
        return None
    # Finnhub uses bare equity tickers (not BTC-USD / =X)
    fh_sym = symbol.strip().upper().replace("$", "")
    if fh_sym in YAHOO_ALIASES:
        # Crypto/FX not supported on stock/candle
        return None
    try:
        d = date.fromisoformat(trade_date)
    except ValueError:
        return None
    start = datetime.combine(d - timedelta(days=5), datetime.min.time(), tzinfo=ET)
    end = datetime.combine(d + timedelta(days=2), datetime.min.time(), tzinfo=ET)
    params = urlencode(
        {
            "symbol": fh_sym,
            "resolution": "D",
            "from": str(int(start.timestamp())),
            "to": str(int(end.timestamp())),
            "token": finnhub_key,
        }
    )
    url = f"https://finnhub.io/api/v1/stock/candle?{params}"
    req = Request(url, headers={"User-Agent": "llm-sentiment-feed/1.0"})
    try:
        with urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode())
    except HTTPError as exc:
        LOG.warning("finnhub candle HTTP %s %s %s: %s", fh_sym, trade_date, exc.code, exc.reason)
        return None
    except (URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        LOG.warning("finnhub candle failed %s %s: %r", fh_sym, trade_date, exc)
        return None
    if data.get("s") != "ok":
        LOG.warning("finnhub candle %s %s status=%s", fh_sym, trade_date, data.get("s"))
        return None
    times = data.get("t") or []
    closes = data.get("c") or []
    for i, ts in enumerate(times):
        if i >= len(closes):
            break
        bar_d = datetime.fromtimestamp(int(ts), tz=ET).date()
        if bar_d == d:
            return float(closes[i])
    return None


def fetch_daily_close(*, finnhub_key: str, symbol: str, trade_date: str) -> float | None:
    """Yahoo first (free), Finnhub candle second (paid tier only)."""
    px = fetch_yahoo_daily_close(symbol=symbol, trade_date=trade_date)
    if px is not None:
        return px
    return fetch_finnhub_daily_close(
        finnhub_key=finnhub_key, symbol=symbol, trade_date=trade_date
    )


def fetch_latest_quote(*, finnhub_key: str, symbol: str) -> float | None:
    """For test runs only — not used for official live scoring."""
    ysym = normalize_symbol(symbol)
    if ysym:
        sym = url_quote(ysym, safe="")
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=1d&range=1d"
        req = Request(url, headers={"User-Agent": "llm-sentiment-feed/1.0"}, method="GET")
        try:
            with urlopen(req, timeout=20) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="replace"))
            res = (data.get("chart") or {}).get("result")
            if res:
                meta = res[0].get("meta") or {}
                px = meta.get("regularMarketPrice")
                if px is not None:
                    return float(px)
        except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError, TypeError, ValueError):
            pass

    if not finnhub_key:
        return None
    fh_sym = symbol.strip().upper().replace("$", "")
    if fh_sym in YAHOO_ALIASES:
        return None
    params = urlencode({"symbol": fh_sym, "token": finnhub_key})
    url = f"https://finnhub.io/api/v1/quote?{params}"
    req = Request(url, headers={"User-Agent": "llm-sentiment-feed/1.0"})
    try:
        with urlopen(req, timeout=20) as resp:
            data = json.loads(resp.read().decode())
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None
    pc = data.get("c")
    return float(pc) if pc is not None else None
