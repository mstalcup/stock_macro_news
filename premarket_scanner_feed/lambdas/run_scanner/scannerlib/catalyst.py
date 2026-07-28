from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote as url_quote
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
LOG = logging.getLogger(__name__)
USER_AGENT = "premarket-scanner-feed/1.0 (+aws-lambda)"


def _get_json(url: str, *, timeout: int = 20) -> dict[str, Any] | None:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        LOG.warning("catalyst GET failed %s: %r", url[:100], exc)
        return None


def _get_text(url: str, *, timeout: int = 20) -> str | None:
    req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "text/html"}, method="GET")
    try:
        with urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except (HTTPError, URLError, TimeoutError, OSError) as exc:
        LOG.warning("catalyst HTML failed %s: %r", url[:100], exc)
        return None


def fetch_finnhub_catalyst(symbol: str, api_key: str) -> dict[str, Any]:
    """Return {catalyst, headlines} from Finnhub company-news (last 3 days)."""
    if not api_key:
        return {"catalyst": None, "headlines": [], "source": "finnhub"}
    today = datetime.now(tz=ET).date()
    start = (today - timedelta(days=3)).isoformat()
    end = today.isoformat()
    sym = url_quote(symbol.upper(), safe="")
    url = (
        f"https://finnhub.io/api/v1/company-news?symbol={sym}"
        f"&from={start}&to={end}&token={api_key}"
    )
    data = _get_json(url)
    if not isinstance(data, list) or not data:
        return {"catalyst": None, "headlines": [], "source": "finnhub"}
    headlines = []
    for row in data[:8]:
        title = (row.get("headline") or "").strip()
        if title and title not in headlines:
            headlines.append(title)
        if len(headlines) >= 2:
            break
    catalyst = None
    if headlines:
        # One-sentence summary from top headline + source
        src = (data[0].get("source") or "").strip()
        catalyst = headlines[0] if not src else f"{headlines[0]} ({src})"
        if len(catalyst) > 220:
            catalyst = catalyst[:217] + "..."
    return {"catalyst": catalyst, "headlines": headlines[:2], "source": "finnhub"}


def fetch_benzinga_catalyst(symbol: str) -> dict[str, Any]:
    """Best-effort Benzinga quote page scrape (may be blocked in Lambda)."""
    sym = url_quote(symbol.upper(), safe="")
    html = _get_text(f"https://www.benzinga.com/quote/{sym}")
    if not html:
        return {"catalyst": None, "headlines": [], "source": "benzinga"}
    headlines: list[str] = []
    # Common headline patterns on Benzinga quote pages
    for pat in (
        r'<a[^>]+class="[^"]*news-title[^"]*"[^>]*>([^<]+)</a>',
        r'data-testid="headline"[^>]*>([^<]+)<',
        r'<h[23][^>]*>([^<]{20,180})</h[23]>',
    ):
        for m in re.finditer(pat, html, re.IGNORECASE):
            t = re.sub(r"\s+", " ", m.group(1)).strip()
            if t and t not in headlines and not t.lower().startswith("benzinga"):
                headlines.append(t)
            if len(headlines) >= 2:
                break
        if len(headlines) >= 2:
            break
    catalyst = headlines[0] if headlines else None
    return {"catalyst": catalyst, "headlines": headlines[:2], "source": "benzinga"}


def lookup_catalyst(symbol: str, *, finnhub_key: str = "") -> dict[str, Any]:
    """Prefer Finnhub; fall back to Benzinga scrape. Never raises."""
    try:
        if finnhub_key:
            fh = fetch_finnhub_catalyst(symbol, finnhub_key)
            if fh.get("catalyst") or fh.get("headlines"):
                return fh
        bz = fetch_benzinga_catalyst(symbol)
        if bz.get("catalyst") or bz.get("headlines"):
            return bz
        return {"catalyst": None, "headlines": [], "source": "none"}
    except Exception as exc:  # noqa: BLE001
        LOG.warning("catalyst lookup failed %s: %r", symbol, exc)
        return {"catalyst": None, "headlines": [], "source": "error"}
