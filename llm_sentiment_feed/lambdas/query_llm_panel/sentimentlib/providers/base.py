from __future__ import annotations

from ..config import PICKS_RESPONSE_FORMAT
from ..parse import extract_json_obj, normalize_picks, require_picks
from ..prompts import build_system_prompt, build_user_prompt
from .gemini import query_gemini
from .grok import query_grok
from .openai import query_openai
from .perplexity import query_perplexity


def query_model(
    *,
    provider: str,
    api_model: str,
    api_key: str,
    issue_date: str,
    context_pack: str,
    use_context: bool,
) -> tuple[dict, str]:
    system = build_system_prompt(use_context=use_context)
    user = build_user_prompt(
        issue_date=issue_date, context_pack=context_pack, use_context=use_context
    )
    if provider == "openai":
        raw = query_openai(api_key=api_key, model=api_model, system=system, user=user)
    elif provider == "perplexity":
        raw = query_perplexity(api_key=api_key, model=api_model, system=system, user=user)
    elif provider == "gemini":
        raw = query_gemini(api_key=api_key, model=api_model, system=system, user=user)
    elif provider == "grok":
        raw = query_grok(api_key=api_key, model=api_model, system=system, user=user)
    else:
        raise ValueError(f"unknown provider {provider!r}")

    parsed = require_picks(normalize_picks(extract_json_obj(raw)), raw=raw)
    return parsed, raw
