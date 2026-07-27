"""Shared types for cross-feed signal matrix."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Literal

Direction = Literal["long", "short"]
SignalKind = Literal["trade", "holding"]


class ConfluenceTier(str, Enum):
    UNANIMOUS = "unanimous"
    STRONG = "strong"
    LEAN = "lean"
    SPLIT = "split"
    SOLO = "solo"
    NONE = "none"


@dataclass(frozen=True)
class SignalVote:
    """
    One normalized vote from a feed.

    source_id: stable id for a column (e.g. llm:openai-gpt-4o, influencer:geeksoffinance).
    channel_id: feed family for overlap counting (macro, llm_sentiment, influencer, hedge_fund).
    kind: trade = directional call; holding = static position context (13F-style, future).
    """

    source_id: str
    channel_id: str
    ticker: str
    direction: Direction | None = None
    kind: SignalKind = "trade"
    weight: float = 1.0
    label: str = ""
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SignalCell:
    """Display cell for one source × ticker."""

    source_id: str
    channel_id: str
    direction: Direction | None
    label: str = ""
    kind: SignalKind = "trade"
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class ChannelRollup:
    """Aggregated vote per channel (used for confluence scoring)."""

    channel_id: str
    trade_long: int = 0
    trade_short: int = 0
    holding_long: int = 0
    holding_short: int = 0
    abstain: int = 0

    @property
    def trade_direction(self) -> Direction | None:
        if self.trade_long > self.trade_short:
            return "long"
        if self.trade_short > self.trade_long:
            return "short"
        return None


@dataclass
class ConfluenceScore:
    tier: ConfluenceTier
    score: float
    long_channels: int = 0
    short_channels: int = 0
    trade_votes_long: int = 0
    trade_votes_short: int = 0
    holding_note: str = ""


@dataclass
class TickerMatrixRow:
    ticker: str
    cells: dict[str, SignalCell] = field(default_factory=dict)
    channels: dict[str, ChannelRollup] = field(default_factory=dict)
    confluence: ConfluenceScore | None = None


@dataclass
class MatrixContext:
    issue_date: str
    slot: str = "pre_open"
    profile: str = ""
    region: str = "us-east-1"
    influencer_user_id: str = "default"
    macro_bucket: str = ""
    macro_stack: str = "macro-news-feed"
    llm_stack: str = "llm-sentiment-feed"
    influencer_stack: str = "influencer-feed"
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ProviderResult:
    provider_id: str
    channel_id: str
    votes: list[SignalVote] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class SignalMatrix:
    issue_date: str
    slot: str
    provider_results: list[ProviderResult] = field(default_factory=list)
    rows: list[TickerMatrixRow] = field(default_factory=list)
    context_meta: dict[str, Any] = field(default_factory=dict)
