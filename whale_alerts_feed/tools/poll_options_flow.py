"""Daily poll: large anonymous options flow + ledger update."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whalelib.backtest import save_json
from whalelib.options_flow_config import POLYGON_API_KEY_ENV
from whalelib.options_ledger import apply_events, sector_rollups
from whalelib.options_providers import polygon_api_key, scan_underlying
from whalelib.options_watchlist import load_watchlist

CACHE = ROOT / "output" / "cache"
WATCHLIST = ROOT / "seed" / "options_watchlist.json"
EVENTS_PATH = CACHE / "options_flow_events.json"
LEDGER_PATH = CACHE / "options_ledger.json"
OI_CACHE_PATH = CACHE / "options_oi_cache.json"
DAILY_PATH = CACHE / "options_flow_daily.json"


def _load_env() -> None:
    for p in (ROOT / ".env", ROOT.parent / ".env", ROOT.parent / "macro_news_feed" / ".env"):
        if not p.is_file():
            continue
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(POLYGON_API_KEY_ENV + "="):
                os.environ[POLYGON_API_KEY_ENV] = line.split("=", 1)[1].strip().strip('"')


def _load_json(path: Path, default):
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="", help="Trade date YYYY-MM-DD (default: today)")
    ap.add_argument("--limit", type=int, default=0, help="Max tickers (0 = full watchlist)")
    ap.add_argument("--block-trades", action="store_true", help="Also fetch Polygon block prints (slow)")
    ap.add_argument("--discord", action="store_true", help="Print Discord-ready summary")
    args = ap.parse_args()

    _load_env()
    trade_date = args.date or date.today().isoformat()
    tickers = load_watchlist(WATCHLIST)
    if args.limit:
        tickers = tickers[: args.limit]

    provider = "polygon" if polygon_api_key() else "yfinance"
    print(f"Options flow poll: {len(tickers)} tickers, provider={provider}, date={trade_date}")

    all_events: list[dict] = []
    for i, t in enumerate(tickers):
        if i and i % 25 == 0:
            print(f"  [{i}/{len(tickers)}] events so far: {len(all_events)}")
        try:
            batch = scan_underlying(t, trade_date=trade_date, fetch_block_trades=args.block_trades)
            for ev in batch:
                ev["event_id"] = f"{trade_date}#{ev.get('contract_symbol') or ''}#{ev.get('underlying')}#{ev.get('strike')}#{ev.get('option_type')}"
            all_events.extend(batch)
        except Exception as exc:
            print(f"  skip {t}: {exc!r}")

    hist = _load_json(EVENTS_PATH, [])
    seen = {e.get("event_id") for e in hist if e.get("event_id")}
    new_events = [e for e in all_events if e.get("event_id") not in seen]
    hist.extend(new_events)

    ledger = _load_json(LEDGER_PATH, {"positions": [], "open_count": 0})
    oi_cache = _load_json(OI_CACHE_PATH, {})
    ledger, alerts = apply_events(ledger, all_events, oi_cache=oi_cache)

    rollup = sector_rollups(all_events)
    daily = {
        "trade_date": trade_date,
        "provider": provider,
        "ticker_count": len(tickers),
        "events": len(all_events),
        "new_events": len(new_events),
        "rollup": rollup,
        "alerts": alerts[:50],
    }

    CACHE.mkdir(parents=True, exist_ok=True)
    save_json(EVENTS_PATH, hist[-50000:])  # cap history
    save_json(LEDGER_PATH, ledger)
    save_json(OI_CACHE_PATH, oi_cache)
    save_json(DAILY_PATH, daily)

    print(f"DONE: {len(all_events)} large-flow contracts, {len(new_events)} new, open ledger={ledger.get('open_count')}")
    if rollup.get("top_underlyings"):
        print("  Top flow:", ", ".join(
            f"{x['ticker']} ${x['premium_usd']/1e6:.1f}M" for x in rollup["top_underlyings"][:8]
        ))

    if args.discord:
        lines = [
            f"**Options flow** ({trade_date}) — anonymous large flow",
            f"Provider: {provider} | Contracts flagged: {len(all_events)} | Put share: {rollup.get('put_share_pct')}%",
            "",
            "**Top underlyings by est. premium:**",
        ]
        for x in rollup.get("top_underlyings", [])[:12]:
            lines.append(f"- {x['ticker']}: ${x['premium_usd']/1e6:.1f}M")
        open_pos = [p for p in ledger.get("positions", []) if p.get("status") == "open"][:8]
        if open_pos:
            lines.append("")
            lines.append("**Tracked open book (inferred):**")
            for p in open_pos:
                lines.append(
                    f"- {p['underlying']} {p['option_type'].upper()} "
                    f"${p.get('strike')} exp {p.get('expiry')} ~${float(p.get('est_premium_usd',0))/1e6:.1f}M"
                )
        print("\n".join(lines))


if __name__ == "__main__":
    main()
