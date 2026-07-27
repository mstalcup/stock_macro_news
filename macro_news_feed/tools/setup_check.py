"""
Preflight: env keys, Secrets Manager, and optional live fetch smoke test.

  py tools/setup_check.py --profile mastalcup
  py tools/setup_check.py --smoke-fetch
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

FEED = Path(__file__).resolve().parents[1]
ROOT = FEED.parent
sys.path.insert(0, str(FEED / "lambdas" / "fetch_news_slot"))

for env_path in (ROOT / ".env", FEED / ".env"):
    if env_path.is_file():
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
    ap.add_argument("--smoke-fetch", action="store_true")
    args = ap.parse_args()

    from newslib.secrets import load_api_keys

    keys = load_api_keys()
    print("Local/env keys:")
    for name, val in keys.items():
        print(f"  {name}: {'SET' if val else 'MISSING'}")

    import boto3

    sm = boto3.Session(profile_name=args.profile, region_name=args.region).client("secretsmanager")
    try:
        raw = sm.get_secret_value(SecretId=f"{args.stack}/news-api-keys")["SecretString"]
        sec = json.loads(raw)
        print("\nAWS secret macro-news-feed/news-api-keys:")
        for k in ("alpha_vantage_api_key", "news_api_key", "finnhub_api_key"):
            v = (sec.get(k) or "").strip()
            print(f"  {k}: {'SET' if v else 'MISSING'}")
    except Exception as exc:
        print(f"\nAWS secret read failed: {exc}")

    try:
        raw_oai = sm.get_secret_value(SecretId=f"{args.stack}/openai-api-key")["SecretString"]
        oai = json.loads(raw_oai)
        oai_key = (oai.get("api_key") or "").strip()
        print(f"\nAWS secret {args.stack}/openai-api-key:")
        print(f"  api_key: {'SET' if oai_key else 'MISSING'}")
    except Exception as exc:
        print(f"\nOpenAI secret read failed: {exc}")

    local_oai = (os.environ.get("OPENAI_API_KEY") or "").strip()
    if local_oai:
        print(f"  local OPENAI_API_KEY: SET")

    try:
        cf = boto3.Session(profile_name=args.profile, region_name=args.region).client("cloudformation")
        outs = {o["OutputKey"]: o["OutputValue"] for o in cf.describe_stacks(StackName=args.stack)["Stacks"][0]["Outputs"]}
        print(f"\nStack {args.stack}: OK")
        print(f"  bucket: {outs.get('NewsArtifactsBucket')}")
        print(f"  lambda: {outs.get('FetchNewsSlotFunction')}")
    except Exception as exc:
        print(f"\nStack check failed: {exc}")

    if args.smoke_fetch:
        from datetime import date

        from newslib.config import ENABLED_PROVIDERS
        from newslib.fetchers import fetch_alpha_vantage, fetch_finnhub, fetch_newsapi
        from newslib.window import issue_window

        issue_date = date.today().isoformat()
        ws, we = issue_window(issue_date)
        print(f"\nSmoke fetch (enabled={ENABLED_PROVIDERS}, issue_date={issue_date} PT day):")
        all_providers = (
            (
                "alpha_vantage",
                lambda: fetch_alpha_vantage(
                    keys["alpha_vantage"], window_start=ws, window_end=we
                ),
            ),
            ("newsapi", lambda: fetch_newsapi(keys["newsapi"], window_start=ws, window_end=we)),
            ("finnhub", lambda: fetch_finnhub(keys["finnhub"], window_start=ws, window_end=we)),
        )
        for label, fn in all_providers:
            if label not in ENABLED_PROVIDERS:
                print(f"  {label}: skipped (disabled)")
                continue
            articles, err = fn()
            line = f"  {label}: {len(articles)} articles"
            if err:
                line += f" — {err[:120]}"
            print(line)


if __name__ == "__main__":
    main()
