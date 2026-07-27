from __future__ import annotations

from ..config import MAX_PICKS, MIN_PICKS
from ..http_util import post_json
from ..parse import extract_json_obj, normalize_picks, require_picks


def _picks_schema() -> dict:
    return {
        "type": "object",
        "properties": {
            "market_bias": {
                "type": "string",
                "enum": ["risk_on", "risk_off", "mixed", "unclear"],
            },
            "picks": {
                "type": "array",
                "minItems": MIN_PICKS,
                "maxItems": MAX_PICKS,
                "items": {
                    "type": "object",
                    "properties": {
                        "ticker": {"type": "string"},
                        "direction": {"type": "string", "enum": ["long", "short"]},
                        "conviction": {
                            "type": "string",
                            "enum": ["high", "medium", "low"],
                        },
                        "themes": {"type": "array", "items": {"type": "string"}},
                        "rationale": {"type": "string"},
                        "catalysts": {"type": "string"},
                    },
                    "required": [
                        "ticker",
                        "direction",
                        "conviction",
                        "themes",
                        "rationale",
                        "catalysts",
                    ],
                },
            },
        },
        "required": ["market_bias", "picks"],
    }


def query_gemini(*, api_key: str, model: str, system: str, user: str) -> str:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    payload = post_json(
        url,
        headers={"Content-Type": "application/json"},
        body={
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {
                "temperature": 0.35,
                "maxOutputTokens": 8192,
                "responseMimeType": "application/json",
                "responseSchema": _picks_schema(),
                "thinkingConfig": {"thinkingBudget": 0},
            },
        },
        timeout=120,
    )
    candidates = payload.get("candidates") or []
    if not candidates:
        block = payload.get("promptFeedback") or {}
        raise RuntimeError(f"gemini empty candidates: {str(payload)[:400]} block={block}")
    cand = candidates[0]
    finish = cand.get("finishReason") or ""
    if finish not in ("STOP", ""):
        raise RuntimeError(f"gemini finishReason={finish}: {str(cand)[:400]}")
    parts = (cand.get("content") or {}).get("parts") or []
    text = "".join((p.get("text") or "") for p in parts)
    require_picks(normalize_picks(extract_json_obj(text)), raw=text)
    return text
