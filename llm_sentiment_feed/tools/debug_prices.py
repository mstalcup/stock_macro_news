"""Compare Yahoo vs Finnhub daily close for scoring diagnostics."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "lambdas" / "score_recommendations"))

from scorerlib.prices import (  # noqa: E402
    fetch_daily_close,
    fetch_finnhub_daily_close,
    fetch_yahoo_daily_close,
    normalize_symbol,
)


def _load_finnhub_key() -> str:
    env_path = _ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.startswith("FINNHUB_API_KEY="):
                return line.split("=", 1)[1].strip()
    return ""


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbol", default="AAPL")
    ap.add_argument("--trade-date", default="2026-05-15")
    ap.add_argument("--finnhub-key", default="")
    args = ap.parse_args()

    key = args.finnhub_key or _load_finnhub_key()
    sym = args.symbol
    td = args.trade_date
    ysym = normalize_symbol(sym)

    yahoo = fetch_yahoo_daily_close(symbol=sym, trade_date=td)
    finnhub = fetch_finnhub_daily_close(finnhub_key=key, symbol=sym, trade_date=td) if key else None
    merged = fetch_daily_close(finnhub_key=key, symbol=sym, trade_date=td)

    print(
        json.dumps(
            {
                "symbol": sym,
                "yahoo_symbol": ysym,
                "trade_date": td,
                "yahoo_close": yahoo,
                "finnhub_close": finnhub,
                "merged_close": merged,
                "note": "Finnhub /stock/candle requires paid tier; free keys only support /news.",
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
