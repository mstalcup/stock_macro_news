from __future__ import annotations

from .config import HORIZON_DAYS, MAX_PICKS, MIN_PICKS, PROMPT_VERSION


def build_system_prompt(*, use_context: bool) -> str:
    ctx = (
        "Use only the attached macro context pack; do not invent headlines, dates, or tickers."
        if use_context
        else "Use your own knowledge; do not invent false dates or tickers."
    )
    return (
        "You are a US equities strategist in a research panel that tracks hypothetical "
        "30-day paper positions (not personalized investment advice). "
        "Output JSON matching the schema exactly. "
        f"{ctx} "
        f"You MUST return between {MIN_PICKS} and {MAX_PICKS} picks — never an empty picks array. "
        "Each pick is a tradable US stock or ETF with an explicit position: "
        '"direction" must be exactly "long" (bullish) or "short" (bearish) for that ticker '
        f"over the next {HORIZON_DAYS} calendar days from same-day close. "
        "Valid tickers only (1-6 letters). "
        "Keep rationale under 120 words and catalysts under 40 words per pick."
    )


def build_user_prompt(
    *,
    issue_date: str,
    context_pack: str,
    use_context: bool,
) -> str:
    mode = "hybrid (macro digest + headlines attached)" if use_context else "native knowledge only"
    return (
        f"Issue date: {issue_date}\n"
        f"Prompt version: {PROMPT_VERSION}\n"
        f"Mode: {mode}\n"
        f"Horizon: {HORIZON_DAYS} calendar days from entry at same-day close.\n\n"
        f"Context pack:\n{context_pack}\n\n"
        f"Return {MIN_PICKS}–{MAX_PICKS} picks. Every pick must include ticker, direction "
        '(long or short), conviction, themes, rationale, and catalysts.'
    )


def build_context_pack(*, digest_markdown: str, headlines: list[dict]) -> str:
    lines = ["=== MACRO DIGEST ===", (digest_markdown or "(none)")[:12000]]
    lines.append("\n=== HEADLINES (deduped) ===")
    for i, art in enumerate(headlines[:20], 1):
        title = (art.get("title") or "").strip()
        summary = (art.get("summary") or "").strip()[:280]
        prov = ",".join(art.get("providers") or [])
        lines.append(f"{i}. [{prov}] {title}\n   {summary}")
    return "\n".join(lines)
