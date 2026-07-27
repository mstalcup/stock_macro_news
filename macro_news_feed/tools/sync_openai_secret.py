"""Push OPENAI_API_KEY from .env to macro-news-feed/openai-api-key secret."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import boto3

FEED = Path(__file__).resolve().parents[1]
ROOT = FEED.parent


def _load_env() -> None:
    for env_path in (ROOT / ".env", FEED / ".env"):
        if not env_path.is_file():
            continue
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--stack", default="macro-news-feed")
    args = ap.parse_args()

    _load_env()
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if not key:
        raise SystemExit("OPENAI_API_KEY missing in .env")

    sm = boto3.Session(profile_name=args.profile, region_name=args.region).client(
        "secretsmanager"
    )
    secret_id = f"{args.stack}/openai-api-key"
    payload = json.dumps({"api_key": key})
    try:
        sm.put_secret_value(SecretId=secret_id, SecretString=payload)
    except sm.exceptions.ResourceNotFoundException:
        sm.create_secret(Name=secret_id, SecretString=payload)
    print(f"Updated {secret_id}")


if __name__ == "__main__":
    main()
