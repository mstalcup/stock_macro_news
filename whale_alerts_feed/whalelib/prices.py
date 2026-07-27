from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from functools import lru_cache
from urllib.error import HTTPError, URLError
from urllib.parse import quote as url_quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

from .backtest_config import EDGAR_USER_AGENT

ET = ZoneInfo("America/New_York")
LOG = logging.getLogger(__name__)


def normalize_symbol(ticker: str) -> str | None:
    t = (ticker or "").strip().upper().replace("$", "")
    if not t or len(t) > 6:
        return None
    if t == "BRK.B":
        return "BRK-B"
    return t


def _yahoo_chart(*, yahoo_symbol: str, period1: int, period2: int) -> tuple[list[date], list[float]] | None:
    sym = url_quote(yahoo_symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        f"?period1={period1}&period2={period2}&interval=1d"
    )
    req = Request(url, headers={"User-Agent": EDGAR_USER_AGENT}, method="GET")
    try:
        with urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        LOG.debug("yahoo chart failed %s: %r", yahoo_symbol, exc)
        return None
    res = (data.get("chart") or {}).get("result")
    if not res:
        return None
    r0 = res[0]
    ts = r0.get("timestamp") or []
    quotes = (r0.get("indicators") or {}).get("quote") or [{}]
    closes = (quotes[0].get("close") if quotes else None) or []
    out_d: list[date] = []
    out_c: list[float] = []
    for i, t in enumerate(ts):
        if i >= len(closes) or closes[i] is None:
            continue
        out_d.append(datetime.fromtimestamp(int(t), tz=ET).date())
        out_c.append(float(closes[i]))
    return (out_d, out_c) if out_d else None


@lru_cache(maxsize=256)
def _load_chart(
    yahoo_symbol: str, start_iso: str, end_iso: str
) -> tuple[tuple[str, ...], tuple[float, ...], tuple[float, ...]]:
    start = date.fromisoformat(start_iso)
    end = date.fromisoformat(end_iso)
    p1 = int(datetime.combine(start, datetime.min.time(), tzinfo=ET).timestamp())
    p2 = int(datetime.combine(end, datetime.min.time(), tzinfo=ET).timestamp())
    sym = url_quote(yahoo_symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        f"?period1={p1}&period2={p2}&interval=1d"
    )
    req = Request(url, headers={"User-Agent": EDGAR_USER_AGENT}, method="GET")
    try:
        with urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        LOG.debug("yahoo chart failed %s: %r", yahoo_symbol, exc)
        return (), (), ()
    res = (data.get("chart") or {}).get("result")
    if not res:
        return (), (), ()
    r0 = res[0]
    ts = r0.get("timestamp") or []
    quotes = (r0.get("indicators") or {}).get("quote") or [{}]
    q0 = quotes[0] if quotes else {}
    closes = q0.get("close") or []
    volumes = q0.get("volume") or []
    out_d: list[date] = []
    out_c: list[float] = []
    out_v: list[float] = []
    for i, t in enumerate(ts):
        if i >= len(closes) or closes[i] is None:
            continue
        out_d.append(datetime.fromtimestamp(int(t), tz=ET).date())
        out_c.append(float(closes[i]))
        vol = volumes[i] if i < len(volumes) and volumes[i] is not None else 0.0
        out_v.append(float(vol))
    if not out_d:
        return (), (), ()
    return (
        tuple(d.isoformat() for d in out_d),
        tuple(out_c),
        tuple(out_v),
    )


@lru_cache(maxsize=256)
def _load_series(yahoo_symbol: str, start_iso: str, end_iso: str) -> tuple[tuple[str, ...], tuple[float, ...]]:
    dates, closes, _ = _load_chart(yahoo_symbol, start_iso, end_iso)
    return dates, closes


def volume_series(symbol: str, start_iso: str, end_iso: str) -> tuple[list[str], list[float]]:
    ysym = normalize_symbol(symbol)
    if not ysym:
        return [], []
    dates, _, volumes = _load_chart(ysym, start_iso, end_iso)
    return list(dates), list(volumes)


def forward_return_pct(symbol: str, anchor_date: str, horizon_days: int) -> float | None:
    ysym = normalize_symbol(symbol)
    if not ysym:
        return None
    try:
        anchor = date.fromisoformat(anchor_date)
    except ValueError:
        return None
    end_cal = anchor + timedelta(days=horizon_days * 2 + 10)
    dates, closes = _load_series(ysym, (anchor - timedelta(days=7)).isoformat(), end_cal.isoformat())
    if not dates:
        return None
    start_idx = None
    for i, ds in enumerate(dates):
        if ds >= anchor_date:
            start_idx = i
            break
    if start_idx is None:
        return None
    end_idx = start_idx + horizon_days
    if end_idx >= len(closes):
        return None
    p0, p1 = closes[start_idx], closes[end_idx]
    if not p0:
        return None
    return round((p1 - p0) / p0 * 100, 4)
