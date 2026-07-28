from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote as url_quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
LOG = logging.getLogger(__name__)
USER_AGENT = "premarket-scanner-feed/1.0 (+aws-lambda)"


def _get_json(url: str, *, timeout: int = 25) -> dict[str, Any] | None:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        LOG.warning("GET failed %s: %r", url[:120], exc)
        return None


def _get_text(url: str, *, timeout: int = 25) -> str | None:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        LOG.warning("GET text failed %s: %r", url[:120], exc)
        return None


def market_issue_date(now: datetime | None = None) -> str:
    """US equity session date in ET (roll back weekends)."""
    et = (now or datetime.now(tz=ET)).astimezone(ET)
    while et.weekday() >= 5:
        et -= timedelta(days=1)
    return et.date().isoformat()


def fetch_day_gainers(count: int = 50) -> list[dict[str, Any]]:
    """Yahoo predefined screener — works premarket and RTH."""
    url = (
        "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"
        f"?formatted=false&lang=en-US&region=US&scrIds=day_gainers&count={count}"
    )
    data = _get_json(url)
    if not data:
        return []
    quotes = ((data.get("finance") or {}).get("result") or [{}])[0].get("quotes") or []
    return [q for q in quotes if isinstance(q, dict) and q.get("symbol")]


def fetch_gainers_page_fallback() -> list[dict[str, Any]]:
    """Best-effort scrape of finance.yahoo.com/markets/stocks/gainers/."""
    html = _get_text("https://finance.yahoo.com/markets/stocks/gainers/")
    if not html:
        return []
    # Embedded JSON blob used by Yahoo UI
    m = re.search(r"root\.App\.main\s*=\s*(\{.*?\});\s*\n", html, re.DOTALL)
    if not m:
        # Newer pages embed quotes differently — pull ticker-like rows from links
        rows = []
        for sym in re.findall(r"/quote/([A-Z][A-Z0-9.\-]{0,8})(?:\?|/|\")", html):
            if sym not in {r["symbol"] for r in rows}:
                rows.append({"symbol": sym})
            if len(rows) >= 50:
                break
        return rows
    try:
        blob = json.loads(m.group(1))
    except json.JSONDecodeError:
        return []
    # Walk for quote lists
    found: list[dict[str, Any]] = []

    def walk(obj: Any) -> None:
        if len(found) >= 50:
            return
        if isinstance(obj, dict):
            if "symbol" in obj and ("regularMarketPrice" in obj or "preMarketPrice" in obj):
                found.append(obj)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(blob)
    return found


def fetch_quote(symbol: str) -> dict[str, Any] | None:
    """Best-effort quote. Prefer v7; fall back to chart meta (Lambda-friendly)."""
    sym = url_quote(symbol.upper(), safe="")
    url = f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={sym}"
    data = _get_json(url)
    if data:
        results = ((data.get("quoteResponse") or {}).get("result") or [])
        if results:
            return results[0]
    # Chart meta fallback — more reliable from AWS IPs
    now = datetime.now(tz=ET)
    period2 = int(now.timestamp())
    period1 = period2 - 10 * 86400
    chart_url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        f"?period1={period1}&period2={period2}&interval=1d&includePrePost=true"
    )
    chart = _get_json(chart_url)
    if not chart:
        return None
    res = (chart.get("chart") or {}).get("result")
    if not res:
        return None
    meta = res[0].get("meta") or {}
    closes = (((res[0].get("indicators") or {}).get("quote") or [{}])[0].get("close") or [])
    prev = None
    for c in reversed(closes[:-1] if len(closes) > 1 else []):
        if c is not None:
            prev = float(c)
            break
    price = meta.get("regularMarketPrice") or meta.get("postMarketPrice") or meta.get("previousClose")
    out: dict[str, Any] = {
        "symbol": symbol.upper(),
        "regularMarketPrice": price,
        "regularMarketPreviousClose": prev or meta.get("chartPreviousClose") or meta.get("previousClose"),
        "regularMarketVolume": meta.get("regularMarketVolume"),
        "preMarketPrice": meta.get("preMarketPrice"),
        "preMarketChangePercent": meta.get("preMarketChangePercent"),
        "preMarketVolume": meta.get("preMarketVolume"),
        "postMarketPrice": meta.get("postMarketPrice"),
    }
    if out["preMarketChangePercent"] is None and out.get("preMarketPrice") and out.get("regularMarketPreviousClose"):
        try:
            out["preMarketChangePercent"] = (
                (float(out["preMarketPrice"]) - float(out["regularMarketPreviousClose"]))
                / float(out["regularMarketPreviousClose"])
                * 100.0
            )
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    if out.get("regularMarketChangePercent") is None and price and out.get("regularMarketPreviousClose"):
        try:
            out["regularMarketChangePercent"] = (
                (float(price) - float(out["regularMarketPreviousClose"]))
                / float(out["regularMarketPreviousClose"])
                * 100.0
            )
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return out


def fetch_daily_bars(symbol: str, *, days: int = 260) -> list[dict[str, Any]]:
    """Daily OHLCV ending today (ET)."""
    now = datetime.now(tz=ET)
    period2 = int(now.timestamp())
    period1 = int((now - timedelta(days=days + 40)).timestamp())
    sym = url_quote(symbol.upper(), safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        f"?period1={period1}&period2={period2}&interval=1d&includePrePost=false"
    )
    data = _get_json(url)
    return _parse_ohlcv(data)


def fetch_intraday_1m(symbol: str) -> list[dict[str, Any]]:
    """1-minute bars including pre/post for today."""
    sym = url_quote(symbol.upper(), safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        f"?range=1d&interval=1m&includePrePost=true"
    )
    data = _get_json(url)
    return _parse_ohlcv(data)


def _parse_ohlcv(data: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not data:
        return []
    res = (data.get("chart") or {}).get("result")
    if not res:
        return []
    r0 = res[0]
    ts = r0.get("timestamp") or []
    quotes = (r0.get("indicators") or {}).get("quote") or [{}]
    q0 = quotes[0] if quotes else {}
    opens = q0.get("open") or []
    highs = q0.get("high") or []
    lows = q0.get("low") or []
    closes = q0.get("close") or []
    volumes = q0.get("volume") or []
    out: list[dict[str, Any]] = []
    for i, t in enumerate(ts):
        c = closes[i] if i < len(closes) else None
        if c is None:
            continue
        dt = datetime.fromtimestamp(int(t), tz=ET)
        out.append(
            {
                "time": dt,
                "open": float(opens[i]) if i < len(opens) and opens[i] is not None else None,
                "high": float(highs[i]) if i < len(highs) and highs[i] is not None else None,
                "low": float(lows[i]) if i < len(lows) and lows[i] is not None else None,
                "close": float(c),
                "volume": float(volumes[i]) if i < len(volumes) and volumes[i] is not None else 0.0,
            }
        )
    return out


def current_price_from_quote(q: dict[str, Any]) -> float | None:
    for key in ("preMarketPrice", "postMarketPrice", "regularMarketPrice"):
        v = q.get(key)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return None


def gap_metrics(q: dict[str, Any]) -> dict[str, float | None]:
    """Compute gap %, price, volume preferring premarket fields."""
    prev = q.get("regularMarketPreviousClose") or q.get("previousClose")
    pm_px = q.get("preMarketPrice")
    px = pm_px if pm_px is not None else q.get("regularMarketPrice")
    gap = q.get("preMarketChangePercent")
    if gap is None:
        gap = q.get("regularMarketChangePercent")
    if gap is None and prev and px:
        try:
            gap = (float(px) - float(prev)) / float(prev) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            gap = None
    vol = q.get("preMarketVolume")
    if vol is None:
        vol = q.get("regularMarketVolume")
    try:
        price_f = float(px) if px is not None else None
    except (TypeError, ValueError):
        price_f = None
    try:
        gap_f = float(gap) if gap is not None else None
    except (TypeError, ValueError):
        gap_f = None
    try:
        vol_f = float(vol) if vol is not None else None
    except (TypeError, ValueError):
        vol_f = None
    return {"price": price_f, "gap_pct": gap_f, "volume": vol_f}
