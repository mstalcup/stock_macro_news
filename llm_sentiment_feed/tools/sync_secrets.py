"""Push LLM panel API keys from .env to Secrets Manager (merges existing)."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import boto3

FEED = Path(__file__).resolve().parents[1]
ROOT = FEED.parent


def _load_env(*, env_file: Path | None = None) -> None:
    """Load env files; later files override earlier (llm_sentiment_feed/.env wins)."""
    paths: list[Path] = []
    if env_file:
        paths.append(env_file)
    paths.extend([ROOT / "macro_news_feed" / ".env", ROOT / ".env", FEED / ".env"])
    seen: set[Path] = set()
    for p in paths:
        p = p.resolve()
        if p in seen or not p.is_file():
            continue
        seen.add(p)
        for line in p.read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            key = k.strip()
            if not key:
                continue
            # Later files overwrite — do not use setdefault (macro .env often has empty placeholders)
            os.environ[key] = v.strip().strip('"').strip("'")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--stack", default="llm-sentiment-feed")
    ap.add_argument("--from-macro-stack", default="macro-news-feed")
    ap.add_argument(
        "--env-file",
        default="",
        help="Override .env path (default: llm_sentiment_feed/.env)",
    )
    args = ap.parse_args()

    env_path = Path(args.env_file) if args.env_file else FEED / ".env"
    _load_env(env_file=env_path)
    sm = boto3.Session(profile_name=args.profile, region_name=args.region).client(
        "secretsmanager"
    )
    secret_id = f"{args.stack}/llm-panel-keys"

    try:
        current = json.loads(sm.get_secret_value(SecretId=secret_id)["SecretString"])
    except sm.exceptions.ResourceNotFoundException:
        current = {}

  # copy missing from macro stacks
    try:
        raw = sm.get_secret_value(SecretId=f"{args.from_macro_stack}/news-api-keys")["SecretString"]
        data = json.loads(raw)
        current.setdefault("finnhub_api_key", (data.get("finnhub_api_key") or "").strip())
    except Exception as exc:
        print(f"optional copy finnhub: {exc}")
    try:
        raw = sm.get_secret_value(SecretId=f"{args.from_macro_stack}/openai-api-key")["SecretString"]
        data = json.loads(raw)
        key = (data.get("api_key") or data.get("OPENAI_API_KEY") or "").strip()
        if key:
            current["openai_api_key"] = key
    except Exception as exc:
        print(f"optional copy openai: {exc}")

    def _env_first(*names: str) -> str:
        for name in names:
            val = (os.environ.get(name) or "").strip()
            if val:
                return val
        return ""

    env_map = {
        "openai_api_key": ("OPENAI_API_KEY",),
        "perplexity_api_key": ("PERPLEXITY_API_KEY", "PERPLEXITY_KEY"),
        "gemini_api_key": ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GEMINI_KEY"),
        "grok_api_key": ("GROK_API_KEY", "XAI_API_KEY", "GROK_KEY"),
        "finnhub_api_key": ("FINNHUB_API_KEY",),
    }
    for sec_key, env_names in env_map.items():
        # Prefer .env over existing secret when .env has a value
        val = _env_first(*env_names)
        if not val:
            val = (current.get(sec_key) or "").strip()
        if val:
            current[sec_key] = val

    payload = json.dumps(current)
    try:
        sm.put_secret_value(SecretId=secret_id, SecretString=payload)
    except sm.exceptions.ResourceNotFoundException:
        sm.create_secret(Name=secret_id, SecretString=payload)

    print(f"Updated {secret_id}:")
    for k in env_map:
        print(f"  {k}: {'SET' if current.get(k) else 'MISSING'}")


if __name__ == "__main__":
    main()
