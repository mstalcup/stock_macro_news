"""Push tokens.json → Secrets Manager robinhood-agent-feed/rh-oauth."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import boto3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default=os.environ.get("AWS_PROFILE", "mastalcup"))
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "us-east-1"))
    ap.add_argument("--stack", default="robinhood-agent-feed")
    ap.add_argument("--tokens", default="tokens.json")
    args = ap.parse_args()

    root = Path(__file__).resolve().parents[1]
    tokens_path = Path(args.tokens)
    if not tokens_path.is_absolute():
        tokens_path = root / tokens_path

    data = json.loads(tokens_path.read_text(encoding="utf-8"))
    access = (data.get("access_token") or "").strip()
    if not access:
        raise SystemExit("tokens.json missing access_token")

    refresh = (data.get("refresh_token") or "").strip()
    device = (data.get("device_token") or "").strip()
    client_id = (data.get("client_id") or "").strip()
    if not refresh:
        print("Warning: refresh_token empty - cannot auto-refresh after access_token expires.")
    if not client_id:
        print("Warning: client_id empty - refresh will fail; re-export from Claude/Cursor MCP.")
    if device:
        print("Note: device_token present (optional for Claude/Cursor MCP refresh).")

    payload = json.dumps(
        {
            "access_token": access,
            "refresh_token": refresh,
            "device_token": device,
            "expires_at": data.get("expires_at") or "",
            "user_uuid": data.get("user_uuid") or "",
            "client_id": client_id,
        }
    )

    sm = boto3.Session(profile_name=args.profile, region_name=args.region).client("secretsmanager")
    secret_id = f"{args.stack}/rh-oauth"
    sm.put_secret_value(SecretId=secret_id, SecretString=payload)
    print(f"Wrote {secret_id} (expires_at={data.get('expires_at') or 'unknown'})")
    print("Next: node tools/invoke_local.js --read-only")


if __name__ == "__main__":
    main()
