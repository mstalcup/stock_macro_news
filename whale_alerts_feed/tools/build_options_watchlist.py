"""Build S&P 500 + ETF options watchlist."""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whalelib.backtest import save_json
from whalelib.options_watchlist import build_watchlist

SEED = ROOT / "seed" / "options_watchlist.json"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=500)
    args = ap.parse_args()
    tickers = build_watchlist(limit=args.limit)
    SEED.parent.mkdir(parents=True, exist_ok=True)
    save_json(SEED, {"tickers": tickers, "count": len(tickers)})
    print(f"Wrote {SEED} ({len(tickers)} tickers)")


if __name__ == "__main__":
    main()
