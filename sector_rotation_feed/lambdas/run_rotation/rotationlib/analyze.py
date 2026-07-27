from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import (
    BENCHMARK,
    DRILL_HOLDINGS_PER_SECTOR,
    DRILL_IN_COUNT,
    DRILL_TOP_MOVERS,
    DRILL_TOP_VOLUME,
    IN_MIN_REL_5D,
    OUT_MAX_REL_5D,
    SECTOR_ETFS,
)
from .prices import avg_volume, fetch_history, pct_return

ET = ZoneInfo("America/New_York")
_HOLDINGS_PATH = Path(__file__).with_name("holdings_data.json")


def latest_us_market_date(as_of: date | None = None) -> date:
    d = as_of or datetime.now(tz=ET).date()
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


@dataclass
class SectorRow:
    ticker: str
    name: str
    rel_1d: float | None
    rel_5d: float | None
    rel_20d: float | None
    rs_above_ma: bool
    rs_mom_days: int
    rotation_score: float
    status: str  # IN | OUT | NEUTRAL


@dataclass
class HoldingRow:
    ticker: str
    ret_1d: float | None
    ret_5d: float | None
    rel_volume: float | None


def _load_holdings() -> dict[str, list[str]]:
    raw = json.loads(_HOLDINGS_PATH.read_text(encoding="utf-8"))
    return {k: list(v)[:DRILL_HOLDINGS_PER_SECTOR] for k, v in raw.items()}


def _align_closes(
  sector_dates: list[date],
  sector_closes: list[float],
  spy_by_date: dict[date, float],
) -> tuple[list[float], list[float]]:
    aligned_sector: list[float] = []
    aligned_spy: list[float] = []
    for d, c in zip(sector_dates, sector_closes):
        p = spy_by_date.get(d)
        if p:
            aligned_sector.append(c)
            aligned_spy.append(p)
    return aligned_sector, aligned_spy


def _rs_series(sector_closes: list[float], spy_closes: list[float]) -> list[float]:
    out: list[float] = []
    for s, p in zip(sector_closes, spy_closes):
        if p:
            out.append(s / p)
    return out


def _rs_momentum_days(rs: list[float], lookback: int = 5) -> int:
    if len(rs) < 2:
        return 0
    tail = rs[-lookback:]
    streak = 0
    for i in range(1, len(tail)):
        if tail[i] > tail[i - 1]:
            streak += 1
        else:
            streak = 0
    return streak


def _compute_sector_row(
    *,
    ticker: str,
    name: str,
    sector_closes: list[float],
    spy_closes: list[float],
    spy_r1: float | None,
    spy_r5: float | None,
    spy_r20: float | None,
) -> SectorRow:
    r1 = pct_return(sector_closes, 1)
    r5 = pct_return(sector_closes, 5)
    r20 = pct_return(sector_closes, 20)
    rel_1d = round(r1 - spy_r1, 3) if r1 is not None and spy_r1 is not None else None
    rel_5d = round(r5 - spy_r5, 3) if r5 is not None and spy_r5 is not None else None
    rel_20d = round(r20 - spy_r20, 3) if r20 is not None and spy_r20 is not None else None

    rs = _rs_series(sector_closes, spy_closes)
    rs_ma = sum(rs[-20:]) / len(rs[-20:]) if len(rs) >= 20 else (sum(rs) / len(rs) if rs else 0)
    rs_above = bool(rs and rs[-1] > rs_ma)
    rs_mom = _rs_momentum_days(rs)

    score = 0.0
    if rel_5d is not None:
        score += rel_5d * 0.45
    if rel_20d is not None:
        score += rel_20d * 0.30
    if rs_above:
        score += 0.8
    score += rs_mom * 0.15
    if rel_1d is not None:
        score += rel_1d * 0.10

    status = "NEUTRAL"
    if rs_above and rel_5d is not None and rel_5d >= IN_MIN_REL_5D and rs_mom >= 2:
        status = "IN"
    elif (not rs_above and rel_5d is not None and rel_5d <= OUT_MAX_REL_5D) or (
        rel_5d is not None and rel_5d < -1.0
    ):
        status = "OUT"

    return SectorRow(
        ticker=ticker,
        name=name,
        rel_1d=rel_1d,
        rel_5d=rel_5d,
        rel_20d=rel_20d,
        rs_above_ma=rs_above,
        rs_mom_days=rs_mom,
        rotation_score=round(score, 3),
        status=status,
    )


def _drill_sector(sector: str, trade_date: date) -> dict:
    holdings_map = _load_holdings()
    tickers = holdings_map.get(sector, [])
    rows: list[HoldingRow] = []
    for t in tickers:
        hist = fetch_history(t, end_date=trade_date)
        if not hist:
            continue
        _, closes, vols = hist
        r1 = pct_return(closes, 1)
        r5 = pct_return(closes, 5)
        avg_v = avg_volume(vols, 20)
        last_v = next((v for v in reversed(vols) if v), None)
        rel_vol = round(last_v / avg_v, 2) if last_v and avg_v else None
        rows.append(HoldingRow(ticker=t, ret_1d=r1, ret_5d=r5, rel_volume=rel_vol))

    by_5d = sorted(rows, key=lambda x: x.ret_5d if x.ret_5d is not None else -999, reverse=True)
    by_vol = sorted(rows, key=lambda x: x.rel_volume if x.rel_volume is not None else -1, reverse=True)
    return {
        "sector": sector,
        "top_movers_5d": [
            {"ticker": r.ticker, "ret_5d": r.ret_5d, "ret_1d": r.ret_1d}
            for r in by_5d[:DRILL_TOP_MOVERS]
        ],
        "top_rel_volume": [
            {"ticker": r.ticker, "rel_volume": r.rel_volume, "ret_1d": r.ret_1d}
            for r in by_vol[:DRILL_TOP_VOLUME]
        ],
    }


def build_report(*, trade_date: date | None = None) -> dict:
    td = trade_date or latest_us_market_date()
    spy_hist = fetch_history(BENCHMARK, end_date=td)
    if not spy_hist:
        raise RuntimeError(f"Could not fetch {BENCHMARK} prices")
    spy_dates, spy_closes, _ = spy_hist
    spy_by_date = dict(zip(spy_dates, spy_closes))
    spy_r1 = pct_return(spy_closes, 1)
    spy_r5 = pct_return(spy_closes, 5)
    spy_r20 = pct_return(spy_closes, 20)

    sectors: list[SectorRow] = []
    for ticker, name in SECTOR_ETFS.items():
        hist = fetch_history(ticker, end_date=td)
        if not hist:
            continue
        sec_dates, closes, _ = hist
        sec_closes, spy_aligned = _align_closes(sec_dates, closes, spy_by_date)
        if len(sec_closes) < 22:
            continue
        sectors.append(
            _compute_sector_row(
                ticker=ticker,
                name=name,
                sector_closes=sec_closes,
                spy_closes=spy_aligned,
                spy_r1=spy_r1,
                spy_r5=spy_r5,
                spy_r20=spy_r20,
            )
        )

    ranked = sorted(sectors, key=lambda s: s.rotation_score, reverse=True)
    explicit_in = [s for s in ranked if s.status == "IN"]
    explicit_out = [s for s in ranked if s.status == "OUT"]
    in_list = explicit_in[:5] if explicit_in else ranked[:3]
    out_list = explicit_out[:5] if explicit_out else list(reversed(ranked))[:3]

    drill_sectors = [s.ticker for s in sorted(in_list, key=lambda x: x.rotation_score, reverse=True)[:DRILL_IN_COUNT]]
    drill_down = [_drill_sector(s, td) for s in drill_sectors]

    return {
        "trade_date": td.isoformat(),
        "benchmark": BENCHMARK,
        "spy_returns": {"1d": spy_r1, "5d": spy_r5, "20d": spy_r20},
        "sectors": [
            {
                "ticker": s.ticker,
                "name": s.name,
                "rel_1d": s.rel_1d,
                "rel_5d": s.rel_5d,
                "rel_20d": s.rel_20d,
                "rs_above_ma": s.rs_above_ma,
                "rs_mom_days": s.rs_mom_days,
                "rotation_score": s.rotation_score,
                "status": s.status,
            }
            for s in ranked
        ],
        "in_sectors": [{"ticker": s.ticker, "name": s.name, "score": s.rotation_score} for s in in_list],
        "out_sectors": [{"ticker": s.ticker, "name": s.name, "score": s.rotation_score} for s in out_list],
        "drill_down": drill_down,
    }
