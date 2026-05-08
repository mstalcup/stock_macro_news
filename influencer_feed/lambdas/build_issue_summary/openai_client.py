"""Minimal OpenAI Chat Completions client (stdlib only)."""

from __future__ import annotations

import json
import random
import time
import urllib.error
import urllib.request


def chat_completion(
    *,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    max_tokens: int = 1024,
    temperature: float = 0.35,
    timeout_s: int = 120,
    response_format: dict | None = None,
) -> str:
    max_attempts = 4
    base_sleep_s = 1.0
    payload = None
    last_err = None
    use_format = response_format

    for attempt in range(1, max_attempts + 1):
        body_obj: dict = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if use_format:
            body_obj["response_format"] = use_format

        body = json.dumps(body_obj).encode("utf-8")
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            err_body = exc.read().decode("utf-8", errors="replace")[:2000]
            retryable = exc.code in (408, 409, 429, 500, 502, 503, 504)
            if exc.code == 400 and use_format and (
                "response_format" in err_body.lower() or "json_schema" in err_body.lower()
            ):
                print(f"openai_client: dropping response_format after HTTP 400: {err_body[:400]}")
                use_format = None
                continue
            last_err = RuntimeError(f"OpenAI HTTP {exc.code}: {err_body}")
            if (not retryable) or attempt >= max_attempts:
                raise last_err from exc
        except urllib.error.URLError as exc:
            last_err = RuntimeError(f"OpenAI URL error: {exc.reason!r}")
            if attempt >= max_attempts:
                raise last_err from exc

        sleep_s = base_sleep_s * (2 ** (attempt - 1)) + random.uniform(0.0, 0.35)
        time.sleep(sleep_s)

    if payload is None:
        raise last_err or RuntimeError("OpenAI request failed with unknown error")

    choices = payload.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenAI empty choices: {json.dumps(payload)[:500]}")
    msg = choices[0].get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content:
        raise RuntimeError(f"OpenAI empty content: {json.dumps(payload)[:500]}")
    return content
