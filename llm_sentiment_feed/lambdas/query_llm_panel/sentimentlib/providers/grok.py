from __future__ import annotations

from ..config import PICKS_RESPONSE_FORMAT
from ..http_util import post_json


def query_grok(*, api_key: str, model: str, system: str, user: str) -> str:
    payload = post_json(
        "https://api.x.ai/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        body={
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.35,
            "max_tokens": 1200,
            "response_format": PICKS_RESPONSE_FORMAT,
        },
        timeout=120,
    )
    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(f"grok empty choices: {str(payload)[:400]}")
    return (choices[0].get("message") or {}).get("content") or ""
