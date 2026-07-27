"""Provider plugin interface for signal matrix."""
from __future__ import annotations

from abc import ABC, abstractmethod

from ..types import MatrixContext, ProviderResult


class SignalProvider(ABC):
    """
    Load votes for one feed family.

    Subclass and register in registry.py to add channels (Discord, hedge 13F, etc.).
    """

    provider_id: str
    channel_id: str
    description: str = ""

    @abstractmethod
    def load(self, ctx: MatrixContext) -> ProviderResult:
        """Return normalized votes for ctx.issue_date (and ctx.slot if applicable)."""
