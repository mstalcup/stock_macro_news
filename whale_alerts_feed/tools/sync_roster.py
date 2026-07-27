"""Push seed/fund_roster.json into DynamoDB FUND# rows."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--table", default="whale-alerts-feed-whales")
    ap.add_argument("--roster", default=str(ROOT / "seed" / "fund_roster.json"))
    args = ap.parse_args()

    roster = json.loads(Path(args.roster).read_text(encoding="utf-8"))
    table = boto3.Session(profile_name=args.profile, region_name=args.region).resource("dynamodb").Table(
        args.table
    )
    n = 0
    for row in roster:
        cik = str(row.get("cik") or row.get("fund_id") or "").lstrip("0")
        if not cik:
            continue
        table.put_item(
            Item={
                "pk": f"FUND#{cik}",
                "sk": "META",
                "cik": cik,
                "fund_name": row.get("fund_name") or "",
                "tier": row.get("tier") or "B",
                "source": row.get("source") or "",
            }
        )
        n += 1
    print(f"Upserted {n} funds to {args.table}")


if __name__ == "__main__":
    main()
