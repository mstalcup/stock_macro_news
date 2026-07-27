"""Register signal providers — add new channels here."""
from __future__ import annotations

from .providers.base import SignalProvider
from .providers.hedge_fund import HedgeFundHoldingsProvider
from .providers.influencer import InfluencerFeedProvider
from .providers.llm_sentiment import LlmSentimentProvider
from .providers.macro import MacroNewsProvider

# provider_id -> class (instantiated per run)
PROVIDER_REGISTRY: dict[str, type[SignalProvider]] = {
    "macro": MacroNewsProvider,
    "llm_sentiment": LlmSentimentProvider,
    "influencer": InfluencerFeedProvider,
    "hedge_fund": HedgeFundHoldingsProvider,
}

DEFAULT_PROVIDER_IDS: tuple[str, ...] = ("macro", "llm_sentiment", "influencer")


def list_providers() -> list[dict[str, str]]:
    out = []
    for pid, cls in sorted(PROVIDER_REGISTRY.items()):
        inst = cls()
        out.append(
            {
                "provider_id": pid,
                "channel_id": inst.channel_id,
                "description": inst.description or "",
            }
        )
    return out


def resolve_providers(provider_ids: list[str] | None = None) -> list[SignalProvider]:
    ids = list(provider_ids) if provider_ids else list(DEFAULT_PROVIDER_IDS)
    unknown = [i for i in ids if i not in PROVIDER_REGISTRY]
    if unknown:
        raise ValueError(f"Unknown providers: {unknown}. Known: {sorted(PROVIDER_REGISTRY)}")
    return [PROVIDER_REGISTRY[i]() for i in ids]
