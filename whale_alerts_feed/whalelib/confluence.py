"""Multi-signal confluence — same ticker, multiple layers within a window."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

from .backtest_config import CONFLUENCE_WINDOW_DAYS
from .types import WhaleSignal


def link_confluence(
    signals: list[WhaleSignal],
    *,
    window_days: int = CONFLUENCE_WINDOW_DAYS,
) -> list[dict]:
    """
    Group signals by ticker; flag windows where 2+ distinct signal_type layers fire.
    Anchor date = earliest signal in the cluster.
    """
    by_ticker: dict[str, list[WhaleSignal]] = defaultdict(list)
    for s in signals:
        if s.ticker:
            by_ticker[s.ticker.upper()].append(s)

    clusters: list[dict] = []
    for ticker, rows in by_ticker.items():
        rows.sort(key=lambda x: x.signal_date or "")
        used: set[int] = set()
        for i, anchor in enumerate(rows):
            if i in used or not anchor.signal_date:
                continue
            try:
                ad = date.fromisoformat(anchor.signal_date)
            except ValueError:
                continue
            cluster = [anchor]
            types = {anchor.signal_type}
            for j, other in enumerate(rows):
                if j == i or j in used or not other.signal_date:
                    continue
                try:
                    od = date.fromisoformat(other.signal_date)
                except ValueError:
                    continue
                if 0 <= (od - ad).days <= window_days:
                    cluster.append(other)
                    types.add(other.signal_type)
            if len(types) < 2:
                continue
            for j, _ in enumerate(rows):
                if rows[j] in cluster:
                    used.add(j)
            clusters.append(
                {
                    "ticker": ticker,
                    "anchor_date": anchor.signal_date,
                    "layers": sorted(types),
                    "layer_count": len(types),
                    "signal_count": len(cluster),
                    "signals": [s.signal_id for s in cluster],
                    "filer_ciks": sorted({s.filer_cik for s in cluster if s.filer_cik}),
                }
            )
    return clusters


def confluence_to_signals(clusters: list[dict]) -> list[WhaleSignal]:
    """Synthetic tradable signal: multi-layer confluence on anchor date."""
    out: list[WhaleSignal] = []
    for c in clusters:
        ticker = c["ticker"]
        ad = c["anchor_date"]
        layers = c["layers"]
        out.append(
            WhaleSignal(
                signal_id=f"confluence#{'+'.join(layers)}#{ad}#{ticker}",
                signal_type="confluence",
                signal_date=ad,
                filer_cik=(c.get("filer_ciks") or [""])[0],
                filer_name="",
                ticker=ticker,
                alert_class="urgent",
                meta={"layers": layers, "layer_count": c["layer_count"]},
            )
        )
    return out
