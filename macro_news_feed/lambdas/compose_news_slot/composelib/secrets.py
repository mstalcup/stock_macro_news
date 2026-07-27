from __future__ import annotations

import json
import os

import boto3


def load_openai_api_key() -> str | None:
    arn = (os.environ.get("OPENAI_SECRET_ARN") or "").strip()
    if arn:
        sm = boto3.client("secretsmanager")
        raw = (sm.get_secret_value(SecretId=arn)["SecretString"] or "").strip()
        if raw.startswith("{"):
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                return raw or None
            key = (data.get("api_key") or data.get("OPENAI_API_KEY") or "").strip()
            return key or None
        return raw or None
    key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    return key or None
