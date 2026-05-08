#!/usr/bin/env python3
"""
Sync proxy URLs from a local file into AWS Secrets Manager as {"proxy_urls":[...]}.

Accepted proxy file formats (one per line):
  - http://user:pass@host:port
  - https://user:pass@host:port
  - host:port:user:pass

Usage:
  py tools/sync_webshare_secret.py ^
    --proxy-file tools/proxies.txt ^
    --secret-arn arn:aws:secretsmanager:...:secret:influencer-feed/webshare-proxy-... ^
    --profile mastalcup ^
    --region us-east-1
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import boto3


def _parse_proxy_line(raw: str) -> str:
    line = raw.strip()
    if not line or line.startswith("#"):
        return ""
    if line.startswith("http://") or line.startswith("https://"):
        return line
    parts = line.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        return f"http://{user}:{password}@{host}:{port}"
    raise ValueError(
        "Invalid proxy line format. Use URL or host:port:user:pass "
        f"(got: {line[:60]!r})"
    )


def _load_proxy_urls(proxy_file: Path) -> list[str]:
    urls: list[str] = []
    with proxy_file.open("r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            try:
                parsed = _parse_proxy_line(line)
            except ValueError as exc:
                raise SystemExit(f"{proxy_file}:{idx}: {exc}") from exc
            if not parsed:
                continue
            urls.append(parsed)
    # de-dup preserve order
    out: list[str] = []
    seen: set[str] = set()
    for url in urls:
        if url in seen:
            continue
        out.append(url)
        seen.add(url)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--proxy-file", required=True, help="Path to proxy list file")
    p.add_argument("--secret-arn", required=True, help="Secrets Manager ARN for webshare proxy secret")
    p.add_argument("--profile", default="mastalcup", help="AWS profile")
    p.add_argument("--region", default="us-east-1", help="AWS region")
    p.add_argument("--dry-run", action="store_true", help="Print result without writing secret")
    args = p.parse_args()

    proxy_file = Path(args.proxy_file)
    if not proxy_file.exists():
        raise SystemExit(f"Proxy file not found: {proxy_file}")

    proxy_urls = _load_proxy_urls(proxy_file)
    if not proxy_urls:
        raise SystemExit(f"No usable proxy URLs found in {proxy_file}")

    payload = {"proxy_urls": proxy_urls}
    print(f"Loaded {len(proxy_urls)} proxy URL(s) from {proxy_file}")
    if args.dry_run:
        print("Dry run only; not writing secret.")
        return

    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    sm = sess.client("secretsmanager")
    sm.put_secret_value(
        SecretId=args.secret_arn,
        SecretString=json.dumps(payload),
    )
    print(f"Updated secret: {args.secret_arn}")


if __name__ == "__main__":
    main()
