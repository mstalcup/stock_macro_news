import json
import os

import boto3


def load_api_keys() -> dict[str, str]:
    """
    Keys from env (local) or Secrets Manager JSON:
    {"alpha_vantage_api_key":"...","news_api_key":"...","finnhub_api_key":"..."}
    Legacy env names ALPHA_VANTAGE_API_KEY etc. also supported.
    """
    arn = (os.environ.get("NEWS_API_KEYS_SECRET_ARN") or "").strip()
    if arn:
        sm = boto3.client("secretsmanager")
        raw = (sm.get_secret_value(SecretId=arn).get("SecretString") or "").strip()
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
                return {
                    "alpha_vantage": (
                        data.get("alpha_vantage_api_key")
                        or data.get("ALPHA_VANTAGE_API_KEY")
                        or ""
                    ).strip(),
                    "newsapi": (data.get("news_api_key") or data.get("NEWS_API_KEY") or "").strip(),
                    "finnhub": (data.get("finnhub_api_key") or data.get("FINNHUB_API_KEY") or "").strip(),
                }
            except json.JSONDecodeError:
                pass

    return {
        "alpha_vantage": (os.environ.get("ALPHA_VANTAGE_API_KEY") or "").strip(),
        "newsapi": (os.environ.get("NEWS_API_KEY") or "").strip(),
        "finnhub": (os.environ.get("FINNHUB_API_KEY") or "").strip(),
    }
