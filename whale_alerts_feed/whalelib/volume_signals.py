"""Unusual volume spikes — standalone or as confluence with EDGAR layers."""
from __future__ import annotations

import statistics
from datetime import date, timedelta

from .backtest_config import VOLUME_LOOKBACK_DAYS, VOLUME_SPIKE_MIN_RATIO
from .prices import volume_series
from .types import WhaleSignal


def detect_volume_spike(
    ticker: str,
    *,
    as_of: str,
    lookback: int = VOLUME_LOOKBACK_DAYS,
    min_ratio: float = VOLUME_SPIKE_MIN_RATIO,
) -> dict | None:
    """Return spike metadata if volume on as_of exceeds min_ratio * trailing median."""
    try:
        anchor = date.fromisoformat(as_of)
    except ValueError:
        return None
    start = (anchor - timedelta(days=lookback * 2 + 10)).isoformat()
    end = (anchor + timedelta(days=5)).isoformat()
    dates, volumes = volume_series(ticker, start, end)
    if not dates:
        return None
    idx = None
    for i, d in enumerate(dates):
        if d >= as_of:
            idx = i
            break
    if idx is None or idx < lookback:
        return None
    today_vol = volumes[idx]
    if today_vol <= 0:
        return None
    hist = [v for v in volumes[idx - lookback : idx] if v and v > 0]
    if len(hist) < lookback // 2:
        return None
    baseline = statistics.median(hist)
    if baseline <= 0:
        return None
    ratio = today_vol / baseline
    if ratio < min_ratio:
        return None
    return {
        "signal_date": dates[idx],
        "ticker": ticker.upper(),
        "volume": today_vol,
        "baseline_median": baseline,
        "ratio": round(ratio, 2),
    }


def volume_spike_signals_for_events(
    events: list[dict],
    *,
    days_before: int = 10,
    min_ratio: float = VOLUME_SPIKE_MIN_RATIO,
) -> list[WhaleSignal]:
    """
    For each EDGAR event (13D/8-K), scan days [-days_before, 0] for volume spikes.
    Tests whether unusual volume preceded the filing (anticipation hypothesis).
    """
    out: list[WhaleSignal] = []
    seen: set[tuple[str, str]] = set()
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
        for offset in range(days_before, -1, -1):
            scan_date = (fd - timedelta(days=offset)).isoformat()
            key = (ticker, scan_date)
            if key in seen:
                continue
            spike = detect_volume_spike(ticker, as_of=scan_date, min_ratio=min_ratio)
            if not spike:
                continue
            seen.add(key)
            out.append(
                WhaleSignal(
                    signal_id=f"volume_spike#{scan_date}#{ticker}",
                    signal_type="volume_spike",
                    signal_date=scan_date,
                    filer_cik=filer_cik,
                    filer_name=filer_name,
                    ticker=ticker,
                    alert_class="primary",
                    meta={
                        "ratio": spike["ratio"],
                        "days_before_filing": offset,
                        "linked_filing_date": filing_date,
                        "linked_signal_type": ev.get("signal_type") or "",
                    },
                )
            )
            break  # earliest spike before filing only
    return out
