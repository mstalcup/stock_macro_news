"""
Upsert YouTube SOURCE rows for the influencer-feed stack (DynamoDB only).

This is NOT macro_news_feed (headlines). Two scheduled feeds:
  - USER#default  — main influencer / equities-tilt digest
  - USER#crypto   — crypto-only digest

Usage:
  py tools/seed_roster.py --profile mastalcup --region us-east-1 --table influencer-feed-influencer-feed
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone

import boto3

NOW = datetime.now(timezone.utc).isoformat()

# Actively polled for USER#default (hourly FindContent + compose windows).
DEFAULT_FEED_ENABLED = [
    {
        "source_id": "maverickofwallstreet",
        "display_name": "The Maverick of Wall Street",
        "channel_id": "UCvk0KB4Ue0vfPqvDzjIAwiQ",
    },
    {
        "source_id": "patrickboyle",
        "display_name": "Patrick Boyle",
        "channel_id": "UCASM0cgfkJxQ1ICmRilfHLw",
    },
    {
        "source_id": "josephcarlson",
        "display_name": "Joseph Carlson",
        "channel_id": "UCbta0n8i6Rljh0obO7HzG9A",
    },
    {
        "source_id": "forwardguidance",
        "display_name": "Forward Guidance",
        "channel_id": "UCkrwgzhIBKccuDsi_SvZtnQ",
    },
    {
        "source_id": "geeksoffinance",
        "display_name": "Geeks of Finance",
        "channel_id": "UCNEAKk8qPF_BHnnipA9EBQg",
    },
    {
        "source_id": "ziptrader",
        "display_name": "ZipTrader",
        "channel_id": "UC0BGhWsIbV7Dm-lsvhdlMbA",
    },
    {
        "source_id": "coreedgetrader",
        "display_name": "CoreEdgeTrader",
        "channel_id": "UCeyyGiEZ7NH-6YixEafqD0Q",
        "channel_handle": "@CoreEdgeTrader",
    },
    {
        "source_id": "allin",
        "display_name": "All-In Podcast",
        "channel_id": "UCESLZhusAkFfsNsApnjF_Cg",
        "channel_handle": "@allin",
    },
]

# Registered on USER#default but not polled (crypto-primary or paused channels).
DEFAULT_FEED_PAUSED = [
    {"source_id": "benjamincowen", "display_name": "Benjamin Cowen", "channel_id": "UCRvqjQPSeaWn-uEx-w0XOIg"},
    {"source_id": "investanswers", "display_name": "InvestAnswers", "channel_id": "UClgJyzwGs-GyaNxUHcLZrkg"},
    {"source_id": "thecompound", "display_name": "The Compound", "channel_handle": "@the-compound-pod"},
]

# Actively polled for USER#crypto only (separate Discord digest).
CRYPTO_FEED_ENABLED = [
    {
        "source_id": "cryptosrus",
        "display_name": "CryptosRUs",
        "channel_id": "UCI7M65p3A-D3P4v5qW8POxQ",
    },
    {
        "source_id": "benjamincowen",
        "display_name": "Benjamin Cowen",
        "channel_id": "UCRvqjQPSeaWn-uEx-w0XOIg",
    },
    {
        "source_id": "investanswers",
        "display_name": "InvestAnswers",
        "channel_id": "UClgJyzwGs-GyaNxUHcLZrkg",
    },
    {
        "source_id": "bankless",
        "display_name": "Bankless",
        "channel_id": "UCAl9Ld79qaZxp9JzEOwd3aA",
    },
    {
        "source_id": "coinbureau",
        "display_name": "Coin Bureau",
        "channel_id": "UCqK_GSMbpiV8spgD3ZGloSw",
    },
    {
        "source_id": "altcoindaily",
        "display_name": "Altcoin Daily",
        "channel_id": "UCbLhGKVY-bJPcawebgtNfbw",
    },
]


def _item(*, pk: str, spec: dict, enabled: bool, feed_label: str) -> dict:
    sid = spec["source_id"]
    row = {
        "pk": pk,
        "sk": f"SOURCE#{sid}",
        "source_id": sid,
        "display_name": spec.get("display_name", sid),
        "platform": "youtube",
        "enabled": enabled,
        "feed": feed_label,
        "updated_at": NOW,
    }
    if spec.get("channel_id"):
        row["channel_id"] = spec["channel_id"]
    if spec.get("channel_handle"):
        row["channel_handle"] = spec["channel_handle"]
    return row


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=None)
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--table", required=True)
    args = ap.parse_args()
    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    table = session.resource("dynamodb").Table(args.table)

    pk_default = "USER#default"
    for spec in DEFAULT_FEED_ENABLED:
        table.put_item(
            Item=_item(pk=pk_default, spec=spec, enabled=True, feed_label="default")
        )
    for spec in DEFAULT_FEED_PAUSED:
        table.put_item(
            Item=_item(pk=pk_default, spec=spec, enabled=False, feed_label="default")
        )

    pk_crypto = "USER#crypto"
    for spec in CRYPTO_FEED_ENABLED:
        table.put_item(
            Item=_item(pk=pk_crypto, spec=spec, enabled=True, feed_label="crypto")
        )

    print(
        f"USER#default: {len(DEFAULT_FEED_ENABLED)} enabled, "
        f"{len(DEFAULT_FEED_PAUSED)} paused"
    )
    print(f"USER#crypto: {len(CRYPTO_FEED_ENABLED)} enabled")


if __name__ == "__main__":
    main()
