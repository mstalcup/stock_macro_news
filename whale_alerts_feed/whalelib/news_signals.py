"""News stake / activist headlines (Finnhub company-news historical)."""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .backtest_config import EDGAR_USER_AGENT, NEWS_STAKE_KEYWORDS
from .types import WhaleSignal

FINNHUB_NEWS = "https://finnhub.io/api/v1/company-news"
_KEYWORD_RE = re.compile("|".join(re.escape(k) for k in NEWS_STAKE_KEYWORDS), re.I)


def _finnhub_key() -> str:
    return (os.environ.get("FINNHUB_API_KEY") or "").strip()


def fetch_company_news(ticker: str, *, start: date, end: date, api_key: str = "") -> list[dict]:
    key = api_key or _finnhub_key()
    if not key:
        return []
    params = urlencode(
        {
            "symbol": ticker.upper(),
            "from": start.isoformat(),
            "to": end.isoformat(),
            "token": key,
        }
    )
    req = Request(f"{FINNHUB_NEWS}?{params}", headers={"User-Agent": EDGAR_USER_AGENT})
    try:
        with urlopen(req, timeout=25) as resp:
            rows = json.loads(resp.read().decode("utf-8", errors="replace"))
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError, OSError):
        return []
    return rows if isinstance(rows, list) else []


def news_stake_signals_for_events(
    events: list[dict],
    *,
    days_before: int = 14,
    api_key: str = "",
) -> list[WhaleSignal]:
    """
    For each EDGAR event, search news in [filing - days_before, filing] for stake keywords.
    Attributes headline to the fund that later filed (hindsight labeling for backtest).
    """
    key = api_key or _finnhub_key()
    if not key:
        return []
    out: list[WhaleSignal] = []
    seen: set[str] = set()
    for ev in events:
        ticker = (ev.get("issuer_ticker") or ev.get("ticker") or "").upper()
        filing_date = ev.get("file_date") or ev.get("signal_date") or ""
        filer_cik = str(ev.get("filer_cik") or "").lstrip("0")
        filer_name = ev.get("filer_name") or ""
        if not ticker or not filing_date:
            continue
        try:
            fd = date.fromisoformat(filing_date)
        except ValueError:
            continue
        start = fd - timedelta(days=days_before)
        articles = fetch_company_news(ticker, start=start, end=fd, api_key=key)
        for art in articles:
            title = art.get("headline") or art.get("title") or ""
            summary = art.get("summary") or ""
            blob = f"{title} {summary}"
            if not _KEYWORD_RE.search(blob):
                continue
            ts = art.get("datetime") or art.get("published_at") or 0
            if isinstance(ts, (int, float)) and ts > 1_000_000_000:
                signal_date = date.fromtimestamp(int(ts)).isoformat()
            else:
                signal_date = filing_date
            aid = str(art.get("id") or art.get("url") or title[:40])
            sid = f"news_stake#{signal_date}#{ticker}#{aid}"
            if sid in seen:
                continue
            seen.add(sid)
            out.append(
                WhaleSignal(
                    signal_id=sid,
                    signal_type="news_stake",
                    signal_date=signal_date,
                    filer_cik=filer_cik,
                    filer_name=filer_name,
                    ticker=ticker,
                    alert_class="primary",
                    meta={
                        "headline": title[:200],
                        "days_before_filing": (fd - date.fromisoformat(signal_date)).days
                        if signal_date <= filing_date
                        else 0,
                        "linked_filing_date": filing_date,
                    },
                )
            )
            break  # first matching headline before filing
    return out
