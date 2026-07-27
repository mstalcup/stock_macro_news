"""Build and print cross-feed signal matrix."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))

from signal_matrix.context import build_context  # noqa: E402
from signal_matrix.matrix import build_matrix, matrix_to_dict  # noqa: E402
from signal_matrix.registry import DEFAULT_PROVIDER_IDS, list_providers  # noqa: E402
from signal_matrix.render import format_console, format_markdown  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--issue-date", default="", help="YYYY-MM-DD (default: today PT)")
    ap.add_argument("--slot", default="pre_open", choices=["pre_open", "pre_close"])
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--user", default="default", help="Influencer USER# id")
    ap.add_argument(
        "--providers",
        default=",".join(DEFAULT_PROVIDER_IDS),
        help="Comma-separated provider ids (see --list-providers)",
    )
    ap.add_argument("--macro-bucket", default="", help="Override S3 bucket")
    ap.add_argument("--hedge-seed", default="", help="Path to hedge holdings JSON (enables hedge_fund)")
    ap.add_argument("--json", action="store_true", help="Print JSON only")
    ap.add_argument("--markdown", action="store_true", help="Print markdown table")
    ap.add_argument("--list-providers", action="store_true")
    ap.add_argument("--max-rows", type=int, default=30)
    args = ap.parse_args()

    if args.list_providers:
        print(json.dumps(list_providers(), indent=2))
        return

    issue_date = args.issue_date.strip()
    if not issue_date:
        issue_date = datetime.now(ZoneInfo("America/Los_Angeles")).date().isoformat()

    provider_ids = [p.strip() for p in args.providers.split(",") if p.strip()]
    extra: dict = {}
    if args.hedge_seed:
        extra["hedge_fund_seed"] = str(Path(args.hedge_seed).resolve())
        if "hedge_fund" not in provider_ids:
            provider_ids.append("hedge_fund")

    ctx = build_context(
        issue_date=issue_date,
        slot=args.slot,
        profile=args.profile,
        region=args.region,
        influencer_user_id=args.user,
        macro_bucket=args.macro_bucket,
        extra=extra,
    )

    matrix = build_matrix(ctx, provider_ids=provider_ids)

    if args.json:
        print(json.dumps(matrix_to_dict(matrix), indent=2))
        return
    if args.markdown:
        print(format_markdown(matrix, max_rows=args.max_rows))
        return
    print(format_console(matrix, max_rows=args.max_rows))


if __name__ == "__main__":
    main()
