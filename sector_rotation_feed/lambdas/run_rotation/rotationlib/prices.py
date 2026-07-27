from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import quote as url_quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
LOG = logging.getLogger(__name__)


def normalize_symbol(ticker: str) -> str | None:
    t = (ticker or "").strip().upper().replace("$", "")
    if not t:
        return None
    if t == "BRK-B":
        return "BRK-B"
    return t


def _yahoo_chart(*, yahoo_symbol: str, period1: int, period2: int) -> tuple[list[date], list[float], list[float | None]] | None:
    sym = url_quote(yahoo_symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        f"?period1={period1}&period2={period2}&interval=1d"
    )
    req = Request(url, headers={"User-Agent": "sector-rotation-feed/1.0"}, method="GET")
    try:
        with urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        LOG.warning("yahoo chart failed %s: %r", yahoo_symbol, exc)
        return None
    res = (data.get("chart") or {}).get("result")
    if not res:
        return None
    r0 = res[0]
    ts = r0.get("timestamp") or []
    quotes = (r0.get("indicators") or {}).get("quote") or [{}]
    q0 = quotes[0] if quotes else {}
    closes = q0.get("close") or []
    volumes = q0.get("volume") or []
    out_d: list[date] = []
    out_c: list[float] = []
    out_v: list[float | None] = []
    for i, t in enumerate(ts):
        if i >= len(closes):
            break
        c = closes[i]
        if c is None:
            continue
        out_d.append(datetime.fromtimestamp(int(t), tz=ET).date())
        out_c.append(float(c))
        v = volumes[i] if i < len(volumes) else None
        out_v.append(float(v) if v is not None else None)
    if not out_d:
        return None
    return out_d, out_c, out_v


def fetch_history(
    symbol: str,
    *,
    end_date: date,
    calendar_days: int = 90,
) -> tuple[list[date], list[float], list[float | None]] | None:
    ysym = normalize_symbol(symbol)
    if not ysym:
        return None
    start = datetime.combine(end_date - timedelta(days=calendar_days), datetime.min.time(), tzinfo=ET)
    end = datetime.combine(end_date + timedelta(days=2), datetime.min.time(), tzinfo=ET)
    return _yahoo_chart(
        yahoo_symbol=ysym,
        period1=int(start.timestamp()),
        period2=int(end.timestamp()),
    )


def pct_return(closes: list[float], days: int) -> float | None:
    if len(closes) <= days:
        return None
    old = closes[-(days + 1)]
    cur = closes[-1]
    if not old:
        return None
    return round((cur - old) / old * 100, 3)


def avg_volume(vols: list[float | None], n: int = 20) -> float | None:
    tail = [v for v in vols[-n:] if v is not None and v > 0]
    if len(tail) < max(5, n // 2):
        return None
    return sum(tail) / len(tail)
