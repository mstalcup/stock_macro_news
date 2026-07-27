"""Print fund rankings from Phase 0 backtest."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--markdown", action="store_true")
    args = ap.parse_args()

    path = ROOT / "output" / "fund_rankings.json"
    if not path.is_file():
        print("Run run_backtest.py first")
        raise SystemExit(1)
    rankings = json.loads(path.read_text(encoding="utf-8"))
    ranked = [r for r in rankings if r.get("status") == "ranked"][: args.top]

    if args.markdown:
        print("| # | Fund | CIK | Score | α20d | link% | n |")
        print("|---|------|-----|-------|------|-------|---|")
        for i, r in enumerate(ranked, 1):
            print(
                f"| {i} | {r['filer_name'][:35]} | {r['filer_cik']} | "
                f"{r.get('composite_score')} | {r.get('median_alpha_20d')} | "
                f"{round((r.get('anticipation_link_rate') or 0) * 100, 1)} | {r.get('signal_count')} |"
            )
    else:
        for i, r in enumerate(ranked, 1):
            print(
                f"{i:2}. {r['filer_name'][:45]:<45} score={r.get('composite_score')} "
                f"α20d={r.get('median_alpha_20d')} link={r.get('anticipation_link_rate')} n={r.get('signal_count')}"
            )


if __name__ == "__main__":
    main()
