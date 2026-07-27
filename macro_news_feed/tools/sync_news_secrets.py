"""
Push API keys from env into Secrets Manager (macro-news-feed stack).

  set ALPHA_VANTAGE_API_KEY=...
  set NEWS_API_KEY=...
  set FINNHUB_API_KEY=...
  py tools/sync_news_secrets.py --profile mastalcup --stack macro-news-feed
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[2]
FEED = Path(__file__).resolve().parents[1]


def _load_dotenv() -> None:
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
    _load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--stack", default="macro-news-feed")
    args = ap.parse_args()

    secret_id = f"{args.stack}/news-api-keys"
    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    sm = sess.client("secretsmanager")

    existing: dict = {}
    try:
        raw = sm.get_secret_value(SecretId=secret_id)["SecretString"]
        existing = json.loads(raw) if raw.startswith("{") else {}
    except sm.exceptions.ResourceNotFoundException:
        pass

    incoming = {
        "alpha_vantage_api_key": os.environ.get("ALPHA_VANTAGE_API_KEY", "").strip(),
        "news_api_key": os.environ.get("NEWS_API_KEY", "").strip(),
        "finnhub_api_key": os.environ.get("FINNHUB_API_KEY", "").strip(),
    }
    merged = {**existing, **{k: v for k, v in incoming.items() if v}}
    still_missing = [k for k, v in merged.items() if not v]
    if still_missing:
        print(f"Warning: still empty in secret: {', '.join(still_missing)}")

    sm.put_secret_value(SecretId=secret_id, SecretString=json.dumps(merged))
    set_keys = [k for k, v in merged.items() if v]
    print(f"Updated {secret_id} (set: {', '.join(set_keys)})")


if __name__ == "__main__":
    main()
