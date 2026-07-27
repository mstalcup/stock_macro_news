"""Build rolled-up 13F portfolio + fund leaderboard rotation from curated roster."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whalelib.backtest import save_json
from whalelib.edgar import build_name_ticker_map
from whalelib.thirteen_f import (
    aggregate_whale_index,
    fund_portfolio_snapshot,
    suggest_tier_rotation,
)

OUT = ROOT / "output"
CACHE = OUT / "cache"
ROSTER = ROOT / "seed" / "fund_roster_curated.json"
RANKINGS = OUT / "curated_fund_rankings.json"


def _write_report(
    path: Path,
    *,
    index: dict,
    snapshots: list[dict],
    rotation: list[dict],
) -> None:
    lines = [
        "# Whale 13F index",
        "",
        f"Contributing funds: {index['contributing_funds']}",
        f"Aggregate AUM (sum of fund totals): ${index['aggregate_aum_usd']:,.0f}",
        f"Rolled-up positions: {index['position_count']}",
        "",
        "## Top 25 positions (synthetic ETF weights)",
        "",
        "| Ticker | Weight % | Funds | Value USD |",
        "|--------|----------|-------|-----------|",
    ]
    for p in index["positions"][:25]:
        lines.append(
            f"| {p['ticker']} | {p['weight_pct']:.2f}% | {p['fund_count']} | "
            f"${p['value_usd']:,.0f} |"
        )
    lines.extend(["", "## Per-fund latest 13F", ""])
    for s in snapshots:
        if not s:
            continue
        lines.append(
            f"- **{s.get('filer_name', s['filer_cik'])}** — filed {s['filing_date']}, "
            f"Q {s.get('period_ending') or '?'}, "
            f"{s['holdings_count']} lines, ${s['total_value_usd']:,.0f}"
        )
    lines.extend(["", "## Tier rotation suggestions", "", "| Fund | Current | Suggested | α20d med |", "|------|---------|-----------|----------|"])
    for r in rotation:
        if r.get("tier_change"):
            a20 = r.get("median_alpha_20d")
            a20s = f"{a20:.2f}%" if a20 is not None else "—"
            lines.append(
                f"| {r['fund_name']} | {r['current_tier']} | {r['suggested_tier']} | {a20s} |"
            )
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--top", type=int, default=25, help="Top positions in report table")
    args = ap.parse_args()

    if not ROSTER.is_file():
        print(f"Missing {ROSTER}")
        raise SystemExit(1)

    roster = json.loads(ROSTER.read_text(encoding="utf-8"))
    f13_hits = []
    f13_path = CACHE / "f13_hits.json"
    if f13_path.is_file():
        f13_hits = json.loads(f13_path.read_text(encoding="utf-8"))

    name_map = build_name_ticker_map()
    snapshots: list[dict] = []
    print(f"Fetching latest 13F for {len(roster)} curated funds...")
    for i, f in enumerate(roster):
        cik = str(f.get("cik") or "").lstrip("0")
        name = f.get("fund_name") or cik
        snap = fund_portfolio_snapshot(cik, f13_hits=f13_hits or None, name_map=name_map)
        if snap:
            snap["fund_name"] = name
            snap["tier"] = f.get("tier")
            snapshots.append(snap)
            print(f"  [{i+1}/{len(roster)}] {name[:40]:<40} {snap['holdings_count']} holdings")
        else:
            print(f"  [{i+1}/{len(roster)}] {name[:40]:<40} (no 13F)")

    index = aggregate_whale_index(snapshots)
    rankings = json.loads(RANKINGS.read_text(encoding="utf-8")) if RANKINGS.is_file() else []
    rotation = suggest_tier_rotation(rankings, roster)

    OUT.mkdir(parents=True, exist_ok=True)
    save_json(OUT / "whale_13f_index.json", index)
    save_json(OUT / "whale_13f_snapshots.json", snapshots)
    save_json(OUT / "fund_leaderboard.json", rotation)
    print(f"Wrote {OUT / 'whale_13f_index.json'} ({index['position_count']} positions)")

    if args.write_report:
        _write_report(
            OUT / "whale_13f_index_report.md",
            index=index,
            snapshots=snapshots,
            rotation=rotation,
        )
        print(f"Wrote {OUT / 'whale_13f_index_report.md'}")


if __name__ == "__main__":
    main()
