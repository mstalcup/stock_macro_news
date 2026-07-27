"""Copy openai-api-key secret from influencer-feed stack to macro-news-feed."""
from __future__ import annotations

import argparse

import boto3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--from-stack", default="influencer-feed")
    ap.add_argument("--to-stack", default="macro-news-feed")
    args = ap.parse_args()

    sm = boto3.Session(profile_name=args.profile, region_name=args.region).client("secretsmanager")
    raw = sm.get_secret_value(SecretId=f"{args.from_stack}/openai-api-key")["SecretString"]
    sm.put_secret_value(SecretId=f"{args.to_stack}/openai-api-key", SecretString=raw)
    print(f"Copied {args.from_stack}/openai-api-key -> {args.to_stack}/openai-api-key")


if __name__ == "__main__":
    main()
