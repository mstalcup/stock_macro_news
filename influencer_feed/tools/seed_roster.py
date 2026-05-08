"""
Upsert SOURCE rows for macro (user_id=default) and crypto (user_id=crypto).

Usage:
  py tools/seed_roster.py --profile mastalcup --region us-east-1 --table influencer-feed-influencer-feed
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

import boto3

NOW = datetime.now(timezone.utc).isoformat()

# Equities / macro — USER#default (no crypto-primary channels)
MACRO_SOURCES = [
    {
        "source_id": "maverickofwallstreet",
        "display_name": "The Maverick of Wall Street",
        "channel_id": "UCvk0KB4Ue0vfPqvDzjIAwiQ",
        "enabled": True,
        "feed": "macro",
    },
    {
        "source_id": "patrickboyle",
        "display_name": "Patrick Boyle",
        "channel_id": "UCASM0cgfkJxQ1ICmRilfHLw",
        "enabled": True,
        "feed": "macro",
    },
    {
        "source_id": "josephcarlson",
        "display_name": "Joseph Carlson",
        "channel_id": "UCbta0n8i6Rljh0obO7HzG9A",
        "enabled": True,
        "feed": "macro",
    },
    {
        "source_id": "forwardguidance",
        "display_name": "Forward Guidance",
        "channel_id": "UCkrwgzhIBKccuDsi_SvZtnQ",
        "enabled": True,
        "feed": "macro",
    },
    {
        "source_id": "thecompound",
        "display_name": "The Compound",
        "channel_handle": "@the-compound-pod",
        "enabled": True,
        "feed": "macro",
    },
    {
        "source_id": "geeksoffinance",
        "display_name": "Geeks of Finance",
        "channel_id": "UCNEAKk8qPF_BHnnipA9EBQg",
        "enabled": True,
        "feed": "macro",
    },
]

MACRO_DISABLED = [
    {"source_id": "benjamincowen", "display_name": "Benjamin Cowen", "channel_id": "UCRvqjQPSeaWn-uEx-w0XOIg"},
    {"source_id": "investanswers", "display_name": "InvestAnswers", "channel_id": "UClgJyzwGs-GyaNxUHcLZrkg"},
    {"source_id": "coreedgetrader", "display_name": "CoreEdgeTrader", "channel_id": "UCwTS1oRWVB7VVGUQRBYn9aQ"},
    {"source_id": "ziptrader", "display_name": "ZipTrader", "channel_id": "UC0BGhWsIbV7Dm-lsvhdlMbA"},
]

CRYPTO_SOURCES = [
    {
        "source_id": "cryptosrus",
        "display_name": "CryptosRUs",
        "channel_id": "UCI7M65p3A-D3P4v5qW8POxQ",
        "enabled": True,
        "feed": "crypto",
    },
    {
        "source_id": "benjamincowen",
        "display_name": "Benjamin Cowen",
        "channel_id": "UCRvqjQPSeaWn-uEx-w0XOIg",
        "enabled": True,
        "feed": "crypto",
    },
    {
        "source_id": "investanswers",
        "display_name": "InvestAnswers",
        "channel_id": "UClgJyzwGs-GyaNxUHcLZrkg",
        "enabled": True,
        "feed": "crypto",
    },
    {
        "source_id": "bankless",
        "display_name": "Bankless",
        "channel_id": "UCAl9Ld79qaZxp9JzEOwd3aA",
        "enabled": True,
        "feed": "crypto",
    },
    {
        "source_id": "coinbureau",
        "display_name": "Coin Bureau",
        "channel_id": "UCqK_GSMbpiV8spgD3ZGloSw",
        "enabled": True,
        "feed": "crypto",
    },
    {
        "source_id": "altcoindaily",
        "display_name": "Altcoin Daily",
        "channel_id": "UCbLhGKVY-bJPcawebgtNfbw",
        "enabled": True,
        "feed": "crypto",
    },
]


def _item(*, pk: str, spec: dict, enabled: bool) -> dict:
    sid = spec["source_id"]
    row = {
        "pk": pk,
        "sk": f"SOURCE#{sid}",
        "source_id": sid,
        "display_name": spec.get("display_name", sid),
        "platform": "youtube",
        "enabled": enabled,
        "updated_at": NOW,
    }
    if spec.get("channel_id"):
        row["channel_id"] = spec["channel_id"]
    if spec.get("channel_handle"):
        row["channel_handle"] = spec["channel_handle"]
    if spec.get("feed"):
        row["feed"] = spec["feed"]
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--table", required=True)
    args = ap.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    table = session.resource("dynamodb").Table(args.table)

    pk_macro = "USER#default"
    for spec in MACRO_SOURCES:
        table.put_item(Item=_item(pk=pk_macro, spec=spec, enabled=True))
    for spec in MACRO_DISABLED:
        table.put_item(Item=_item(pk=pk_macro, spec=spec, enabled=False))

    pk_crypto = "USER#crypto"
    for spec in CRYPTO_SOURCES:
        table.put_item(Item=_item(pk=pk_crypto, spec=spec, enabled=True))

    print(f"Wrote {len(MACRO_SOURCES)} macro + disabled {len(MACRO_DISABLED)} on {pk_macro}")
    print(f"Wrote {len(CRYPTO_SOURCES)} sources on {pk_crypto}")


if __name__ == "__main__":
    main()
