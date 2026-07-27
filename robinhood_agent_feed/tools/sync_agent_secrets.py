"""Push Anthropic API key from .env → robinhood-agent-feed/agent-keys secret."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import boto3


def load_dotenv(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "mastalcup"))
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--stack", default="robinhood-agent-feed")
    ap.add_argument("--env-file", default=".env")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    env = load_dotenv(root / args.env_file)
    anthropic = (env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY") or "").strip()
    if not anthropic:
        raise SystemExit("Set ANTHROPIC_API_KEY in .env")

    sm = boto3.Session(profile_name=args.profile, region_name=args.region).client("secretsmanager")
    secret_id = f"{args.stack}/agent-keys"
    payload = json.dumps({"anthropic_api_key": anthropic})
    sm.put_secret_value(SecretId=secret_id, SecretString=payload)
    print(f"Updated {secret_id}")


if __name__ == "__main__":
    main()
