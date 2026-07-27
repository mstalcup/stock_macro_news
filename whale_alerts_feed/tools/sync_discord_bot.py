"""Copy bot_token from influencer-feed and set whale-alerts channel_id."""
from __future__ import annotations

import argparse
import json

import boto3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--channel-id", required=True)
    ap.add_argument("--from-stack", default="influencer-feed")
    ap.add_argument("--to-stack", default="whale-alerts-feed")
    args = ap.parse_args()

    sm = boto3.Session(profile_name=args.profile, region_name=args.region).client("secretsmanager")
    src = json.loads(sm.get_secret_value(SecretId=f"{args.from_stack}/discord-bot")["SecretString"])
    token = (src.get("bot_token") or "").strip()
    if not token:
        raise SystemExit(f"No bot_token in {args.from_stack}/discord-bot")
    payload = json.dumps({"bot_token": token, "channel_id": args.channel_id})
    sm.put_secret_value(SecretId=f"{args.to_stack}/discord-bot", SecretString=payload)
    print(f"Updated {args.to_stack}/discord-bot channel_id={args.channel_id}")


if __name__ == "__main__":
    main()
