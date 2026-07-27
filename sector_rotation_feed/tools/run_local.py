"""Run sector rotation report locally (no Discord unless --post)."""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "lambdas" / "run_rotation"
sys.path.insert(0, str(ROOT))

from rotationlib.analyze import build_report  # noqa: E402
from rotationlib.discord_format import format_messages  # noqa: E402


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except Exception:
            pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--trade-date", default="", help="YYYY-MM-DD (default latest US session)")
    ap.add_argument("--post", action="store_true", help="Post via AWS secrets (needs credentials)")
    args = ap.parse_args()

    td = date.fromisoformat(args.trade_date) if args.trade_date else None
    report = build_report(trade_date=td)
    for msg in format_messages(report):
        print(msg)
        print("\n---\n")

    if args.post:
        os.environ.setdefault("AWS_PROFILE", "mastalcup")
        os.environ["ROTATION_TABLE_NAME"] = os.environ.get("ROTATION_TABLE_NAME", "")
        os.environ["DISCORD_BOT_SECRET_ARN"] = f"arn:aws:secretsmanager:us-east-1:secret:sector-rotation-feed/discord-bot"
        import handler as h  # noqa: E402

        out = h.handler({"trade_date": report["trade_date"]}, None)
        print("handler:", out)


if __name__ == "__main__":
    main()
