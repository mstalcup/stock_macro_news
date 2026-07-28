"""Local runner for premarket scanner (no Discord by default)."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "lambdas" / "run_scanner"))

from handler import handler  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["gappers", "tjl", "both"], default="gappers")
    ap.add_argument("--discord", action="store_true", help="Post to Discord (needs AWS secrets)")
    ap.add_argument("--force", action="store_true", help="Ignore TJL time window")
    ap.add_argument("--symbols", nargs="*", help="Override TJL universe")
    ap.add_argument("--out", default="", help="Write JSON to path")
    args = ap.parse_args()

    # Allow local Finnhub key
    if not os.environ.get("NEWS_KEYS_SECRET_ARN") and not os.environ.get("FINNHUB_API_KEY"):
        env_path = ROOT.parent / "macro_news_feed" / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.startswith("FINNHUB_API_KEY="):
                    os.environ["FINNHUB_API_KEY"] = line.split("=", 1)[1].strip().strip('"')

    event = {
        "mode": args.mode,
        "skip_discord": not args.discord,
        "force": args.force,
    }
    if args.symbols:
        event["symbols"] = args.symbols

    result = handler(event, None)
    body = result.get("body")
    parsed = json.loads(body) if isinstance(body, str) else result
    text = json.dumps(parsed, indent=2, default=str)
    print(text)
    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
