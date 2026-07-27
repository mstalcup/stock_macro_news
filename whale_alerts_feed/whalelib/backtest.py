from __future__ import annotations

import json
import logging
import math
import statistics
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from .backtest_config import (
    ANTICIPATION_LOOKBACK_DAYS,
    HORIZONS,
    MIN_SIGNALS_TO_RANK,
    RANK_WEIGHTS,
)
from .prices import forward_return_pct, normalize_symbol
from .types import WhaleSignal

LOG = logging.getLogger(__name__)

ANTICIPATORY_TYPES = frozenset({"13d_new", "13d_increase", "13g_new", "13g_increase", "8k_stake", "news_stake"})
CONFIRMATION_TYPES = frozenset({"13f_new_position", "13f_major_increase"})


def _median(vals: list[float]) -> float | None:
    return statistics.median(vals) if vals else None


def compute_forward_alphas(signals: list[WhaleSignal], spy_ticker: str = "SPY") -> list[dict]:
    out: list[dict] = []
    for sig in signals:
        if not sig.ticker or not normalize_symbol(sig.ticker):
            continue
        row: dict = {
            "signal_id": sig.signal_id,
            "signal_type": sig.signal_type,
            "signal_date": sig.signal_date,
            "filer_cik": sig.filer_cik,
            "filer_name": sig.filer_name,
            "ticker": sig.ticker,
            "alert_class": sig.alert_class,
        }
        for h in HORIZONS:
            stock_r = forward_return_pct(sig.ticker, sig.signal_date, h)
            spy_r = forward_return_pct(spy_ticker, sig.signal_date, h)
            row[f"stock_{h}d"] = stock_r
            row[f"spy_{h}d"] = spy_r
            if stock_r is not None and spy_r is not None:
                row[f"alpha_{h}d"] = round(stock_r - spy_r, 4)
            else:
                row[f"alpha_{h}d"] = None
        out.append(row)
    return out


def link_anticipation(
    anticipatory: list[WhaleSignal],
    thirteen_f: list[WhaleSignal],
    lookback_days: int = ANTICIPATION_LOOKBACK_DAYS,
) -> list[dict]:
    """For each 13F new position, find prior anticipatory signal same fund+ticker."""
    anti_index: dict[tuple[str, str], list[WhaleSignal]] = defaultdict(list)
    for s in anticipatory:
        if s.signal_type in ANTICIPATORY_TYPES and s.ticker:
            anti_index[(s.filer_cik, s.ticker.upper())].append(s)

    links: list[dict] = []
    for f in thirteen_f:
        if f.signal_type not in CONFIRMATION_TYPES or not f.ticker:
            continue
        qend = f.quarter_end or f.signal_date
        try:
            qd = date.fromisoformat(qend)
        except ValueError:
            continue
        key = (f.filer_cik, f.ticker.upper())
        candidates = [
            a
            for a in anti_index.get(key, [])
            if a.signal_date
            and date.fromisoformat(a.signal_date) <= qd
            and (qd - date.fromisoformat(a.signal_date)).days <= lookback_days
        ]
        if not candidates:
            links.append(
                {
                    "filer_cik": f.filer_cik,
                    "filer_name": f.filer_name,
                    "ticker": f.ticker,
                    "quarter_end": qend,
                    "filing_date": f.signal_date,
                    "linked": False,
                    "earliest_signal_date": None,
                    "lead_days": None,
                }
            )
            continue
        earliest = min(candidates, key=lambda x: x.signal_date)
        lead = (qd - date.fromisoformat(earliest.signal_date)).days
        links.append(
            {
                "filer_cik": f.filer_cik,
                "filer_name": f.filer_name,
                "ticker": f.ticker,
                "quarter_end": qend,
                "filing_date": f.signal_date,
                "linked": True,
                "earliest_signal_date": earliest.signal_date,
                "earliest_signal_type": earliest.signal_type,
                "lead_days": lead,
            }
        )
    return links


def rank_funds(
    scored: list[dict],
    links: list[dict],
    *,
    signal_types: frozenset[str] | None = None,
) -> list[dict]:
    types = signal_types or ANTICIPATORY_TYPES
    by_fund: dict[str, list[dict]] = defaultdict(list)
    for row in scored:
        if row.get("signal_type") in types:
            by_fund[row.get("filer_cik") or ""].append(row)

    link_by_fund: dict[str, list[dict]] = defaultdict(list)
    for lk in links:
        link_by_fund[lk.get("filer_cik") or ""].append(lk)

    rankings: list[dict] = []
    for cik, rows in by_fund.items():
        if not cik:
            continue
        name = rows[0].get("filer_name") or cik
        alphas_20 = [r["alpha_20d"] for r in rows if r.get("alpha_20d") is not None]
        alphas_60 = [r["alpha_60d"] for r in rows if r.get("alpha_60d") is not None]
        if len(rows) < MIN_SIGNALS_TO_RANK:
            rankings.append(
                {
                    "filer_cik": cik,
                    "filer_name": name,
                    "signal_count": len(rows),
                    "status": "insufficient_data",
                }
            )
            continue
        hit_rate = sum(1 for a in alphas_20 if a > 0) / len(alphas_20) if alphas_20 else 0
        fund_links = link_by_fund.get(cik, [])
        link_rate = (
            sum(1 for lk in fund_links if lk.get("linked")) / len(fund_links) if fund_links else 0
        )
        med20 = _median(alphas_20) or 0.0
        med60 = _median(alphas_60) or 0.0
        composite = (
            RANK_WEIGHTS["median_alpha_20d"] * med20
            + RANK_WEIGHTS["median_alpha_60d"] * med60
            + RANK_WEIGHTS["anticipation_link_rate"] * (link_rate * 100)
            + RANK_WEIGHTS["hit_rate_20d"] * (hit_rate * 100)
            + RANK_WEIGHTS["signal_count_log"] * math.log(len(rows) + 1)
        )
        filing_alphas_1 = [
            r.get("alpha_1d")
            for r in scored
            if r.get("filer_cik") == cik and r.get("signal_type") in CONFIRMATION_TYPES
        ]
        filing_alphas_1 = [a for a in filing_alphas_1 if a is not None]
        rankings.append(
            {
                "filer_cik": cik,
                "filer_name": name,
                "signal_count": len(rows),
                "status": "ranked",
                "median_alpha_20d": round(med20, 4),
                "median_alpha_60d": round(med60, 4),
                "hit_rate_20d": round(hit_rate, 4),
                "anticipation_link_rate": round(link_rate, 4),
                "median_filing_day_alpha_1d": round(_median(filing_alphas_1) or 0, 4),
                "composite_score": round(composite, 4),
            }
        )
    rankings.sort(key=lambda x: x.get("composite_score") or -999, reverse=True)
    return rankings


def build_roster_from_rankings(
    rankings: list[dict],
    *,
    top_n: int = 50,
    manual_overrides: list[dict] | None = None,
) -> list[dict]:
    roster: list[dict] = []
    seen: set[str] = set()
    ranked = [r for r in rankings if r.get("status") == "ranked"][:top_n]
    for i, r in enumerate(ranked):
        cik = r["filer_cik"]
        if cik in seen:
            continue
        seen.add(cik)
        tier = "S" if i < 10 else "A" if i < 25 else "B"
        roster.append(
            {
                "fund_id": cik,
                "fund_name": r["filer_name"],
                "cik": cik,
                "tier": tier,
                "composite_score": r.get("composite_score"),
                "source": "backtest_rank",
            }
        )
    for ov in manual_overrides or []:
        cik = str(ov.get("cik") or ov.get("fund_id") or "").lstrip("0")
        if not cik or cik in seen:
            continue
        seen.add(cik)
        roster.insert(
            0,
            {
                "fund_id": cik,
                "fund_name": ov.get("fund_name", cik),
                "cik": cik,
                "tier": ov.get("tier", "S"),
                "source": "manual_override",
                "override_reason": ov.get("override_reason", ""),
            },
        )
    return roster


def write_curated_backtest_report(
    path: Path,
    *,
    funds: list[dict],
    scored: list[dict],
    rankings: list[dict],
) -> None:
    """Report for tracked roster only — answers 'would layer-1 alerts have worked?'"""
    by_cik = {str(r.get("filer_cik", "")): r for r in rankings}
    all_alpha_20 = [r["alpha_20d"] for r in scored if r.get("alpha_20d") is not None]
    all_alpha_60 = [r["alpha_60d"] for r in scored if r.get("alpha_60d") is not None]
    hit_20 = sum(1 for a in all_alpha_20 if a > 0) / len(all_alpha_20) if all_alpha_20 else 0
    med20 = _median(all_alpha_20)
    med60 = _median(all_alpha_60)

    lines = [
        "# Curated fund backtest — layer 1 (13D/G filing alerts)",
        "",
        f"Generated: {datetime.utcnow().isoformat()}Z",
        f"Tracked funds: {len(funds)}",
        f"Scored anticipatory signals: {len(scored)}",
        "",
        "## Bottom line (all curated fund 13D/G alerts)",
        "",
    ]
    if not scored:
        lines.append("_No scorable 13D/G signals found for curated funds in SEC submissions window._")
    else:
        lines.extend(
            [
                f"- **Median excess return vs SPY @ 20d:** {med20}%",
                f"- **Median excess return vs SPY @ 60d:** {med60}%",
                f"- **Hit rate @ 20d** (beat SPY): {round(hit_20 * 100, 1)}%",
                "",
                "_Positive median alpha + hit rate > 50% = layer-1 would have been useful on average._",
                "_Per-fund n can be small; treat low-n funds as inconclusive._",
            ]
        )

    lines.extend(["", "## Per fund", "", "| Fund | Signals | med α20d | hit% @20d | med α60d |", "|------|---------|----------|-----------|----------|"])
    for f in funds:
        cik = str(f.get("cik") or "").lstrip("0")
        name = f.get("fund_name") or cik
        r = by_cik.get(cik) or {}
        if r.get("status") == "ranked":
            hr = round((r.get("hit_rate_20d") or 0) * 100, 1)
            lines.append(
                f"| {name[:40]} | {r.get('signal_count')} | {r.get('median_alpha_20d')} | {hr} | {r.get('median_alpha_60d')} |"
            )
        else:
            n = sum(1 for s in scored if s.get("filer_cik") == cik)
            lines.append(f"| {name[:40]} | {n} | — | — | — |")

    lines.extend(["", "## Sample signals (latest 15)", ""])
    for row in sorted(scored, key=lambda x: x.get("signal_date") or "", reverse=True)[:15]:
        a20 = row.get("alpha_20d")
        lines.append(
            f"- **{row.get('signal_date')}** {row.get('filer_name','')[:30]} → "
            f"`{row.get('ticker')}` ({row.get('signal_type')}) α20d={a20}%"
        )

    path.write_text("\n".join(lines), encoding="utf-8")


def write_backtest_report(
    path: Path,
    *,
    rankings: list[dict],
    links: list[dict],
    scored_count: int,
) -> None:
    lines = [
        "# Whale alerts backtest report",
        "",
        f"Generated: {datetime.utcnow().isoformat()}Z",
        f"Scored anticipatory signals: {scored_count}",
        "",
        "## Top funds (anticipatory composite)",
        "",
        "| Rank | Fund | Score | med α20d | link rate | n | filing-day α1d |",
        "|------|------|-------|----------|-----------|---|----------------|",
    ]
    ranked = [r for r in rankings if r.get("status") == "ranked"]
    for i, r in enumerate(ranked[:30], 1):
        lines.append(
            f"| {i} | {r['filer_name'][:40]} | {r.get('composite_score')} | "
            f"{r.get('median_alpha_20d')} | {r.get('anticipation_link_rate')} | "
            f"{r.get('signal_count')} | {r.get('median_filing_day_alpha_1d')} |"
        )
    linked = sum(1 for lk in links if lk.get("linked"))
    lines.extend(
        [
            "",
            f"## Anticipation links: {linked}/{len(links)} 13F new positions had prior anticipatory signal",
            "",
            "_Roster gate uses anticipatory metrics only. Weak filing-day α1d is expected (bot crowding)._",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")


def save_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def layer_summary(scored: list[dict], *, layer: str) -> dict:
    """Aggregate metrics for one signal layer."""
    rows = [r for r in scored if r.get("signal_type") == layer or (layer == "schedule_13dg" and (r.get("signal_type") or "").startswith("13"))]
    a20 = [r["alpha_20d"] for r in rows if r.get("alpha_20d") is not None]
    a5 = [r["alpha_5d"] for r in rows if r.get("alpha_5d") is not None]
    return {
        "layer": layer,
        "scored": len(rows),
        "with_alpha_20d": len(a20),
        "median_alpha_5d": round(_median(a5) or 0, 4) if a5 else None,
        "median_alpha_20d": round(_median(a20) or 0, 4) if a20 else None,
        "hit_rate_20d": round(sum(1 for x in a20 if x > 0) / len(a20), 4) if a20 else None,
    }


def write_phase2_report(
    path: Path,
    *,
    layer_stats: list[dict],
    confluence_scored: list[dict],
    clusters: list[dict],
) -> None:
    lines = [
        "# Phase 2 multi-layer backtest",
        "",
        f"Generated: {datetime.utcnow().isoformat()}Z",
        "",
        "## Per-layer performance (excess vs SPY)",
        "",
        "| Layer | n | med a5d | med a20d | hit% @20d |",
        "|-------|---|---------|----------|-----------|",
    ]
    for s in layer_stats:
        hr = round((s.get("hit_rate_20d") or 0) * 100, 1) if s.get("hit_rate_20d") is not None else "—"
        lines.append(
            f"| {s.get('layer')} | {s.get('with_alpha_20d', 0)} | "
            f"{s.get('median_alpha_5d', '—')} | {s.get('median_alpha_20d', '—')} | {hr} |"
        )
    cf = layer_summary(confluence_scored, layer="confluence")
    lines.extend(
        [
            "",
            "## Confluence (2+ layers same ticker within window)",
            "",
            f"- Clusters found: **{len(clusters)}**",
            f"- Scored confluence anchors: **{cf.get('with_alpha_20d', 0)}**",
        ]
    )
    if cf.get("with_alpha_20d"):
        lines.append(
            f"- Median alpha @20d: **{cf.get('median_alpha_20d')}%**, hit rate: **{round((cf.get('hit_rate_20d') or 0)*100, 1)}%**"
        )
    lines.extend(
        [
            "",
            "## How to read this",
            "",
            "- **8k_stake**: ownership language in fund 8-K filings.",
            "- **volume_spike**: unusual volume in days before a known 13D/8-K (anticipation test).",
            "- **news_stake**: activist/stake headlines before filing (Finnhub).",
            "- **confluence**: 2+ layers on same ticker — candidate for urgent alerts.",
        ]
    )
    path.write_text("\n".join(lines), encoding="utf-8")
