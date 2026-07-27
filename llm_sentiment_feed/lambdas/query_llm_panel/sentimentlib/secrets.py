from __future__ import annotations

import json
import os

import boto3


def load_keys() -> dict[str, str]:
    arn = (os.environ.get("LLM_PANEL_KEYS_SECRET_ARN") or "").strip()
    if arn:
        raw = boto3.client("secretsmanager").get_secret_value(SecretId=arn)["SecretString"]
        data = json.loads(raw)
        return {
            "openai": (data.get("openai_api_key") or "").strip(),
            "perplexity": (data.get("perplexity_api_key") or "").strip(),
            "gemini": (data.get("gemini_api_key") or "").strip(),
            "grok": (data.get("grok_api_key") or "").strip(),
            "finnhub": (data.get("finnhub_api_key") or "").strip(),
        }
    return {
        "openai": (os.environ.get("OPENAI_API_KEY") or "").strip(),
        "perplexity": (os.environ.get("PERPLEXITY_API_KEY") or "").strip(),
        "gemini": (os.environ.get("GEMINI_API_KEY") or "").strip(),
        "grok": (os.environ.get("GROK_API_KEY") or "").strip(),
        "finnhub": (os.environ.get("FINNHUB_API_KEY") or "").strip(),
    }
