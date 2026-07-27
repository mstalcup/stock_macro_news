import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signal_matrix.matrix import score_confluence
from signal_matrix.types import ChannelRollup, ConfluenceTier


def test_score_unanimous_three_channels():
    channels = {
        "macro": ChannelRollup("macro", trade_long=1, trade_short=0),
        "llm_sentiment": ChannelRollup("llm_sentiment", trade_long=3, trade_short=0),
        "influencer": ChannelRollup("influencer", trade_long=4, trade_short=1),
    }
    s = score_confluence(channels)
    assert s.tier == ConfluenceTier.UNANIMOUS
    assert s.long_channels == 3
    assert s.short_channels == 0


def test_score_split():
    channels = {
        "macro": ChannelRollup("macro", trade_long=1, trade_short=0),
        "llm_sentiment": ChannelRollup("llm_sentiment", trade_long=0, trade_short=2),
    }
    s = score_confluence(channels)
    assert s.tier == ConfluenceTier.SPLIT


def test_hedge_holding_does_not_force_split():
    channels = {
        "macro": ChannelRollup("macro", trade_long=1, trade_short=0),
        "llm_sentiment": ChannelRollup("llm_sentiment", trade_long=2, trade_short=0),
        "hedge_fund": ChannelRollup("hedge_fund", holding_long=1, holding_short=0),
    }
    s = score_confluence(channels)
    assert s.tier in (ConfluenceTier.LEAN, ConfluenceTier.STRONG, ConfluenceTier.UNANIMOUS)
    assert s.holding_note
