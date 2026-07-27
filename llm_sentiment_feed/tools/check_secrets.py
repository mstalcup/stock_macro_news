"""Verify llm-panel-keys in Secrets Manager (SET/MISSING only, no values)."""
from __future__ import annotations

import argparse
import json

import boto3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--stack", default="llm-sentiment-feed")
    args = ap.parse_args()

    sm = boto3.Session(profile_name=args.profile, region_name=args.region).client(
        "secretsmanager"
    )
    sec = json.loads(
        sm.get_secret_value(SecretId=f"{args.stack}/llm-panel-keys")["SecretString"]
    )
    for k in (
        "openai_api_key",
        "perplexity_api_key",
        "gemini_api_key",
        "grok_api_key",
        "finnhub_api_key",
    ):
        print(f"  {k}: {'SET' if (sec.get(k) or '').strip() else 'MISSING'}")


if __name__ == "__main__":
    main()
