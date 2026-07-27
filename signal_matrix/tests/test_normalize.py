import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from signal_matrix.normalize import normalize_direction, normalize_ticker


def test_normalize_ticker():
    assert normalize_ticker("nvda") == "NVDA"
    assert normalize_ticker("BTCUSD") == "BTC"
    assert normalize_ticker("") == ""


def test_normalize_direction():
    assert normalize_direction("bullish") == "long"
    assert normalize_direction("bearish") == "short"
    assert normalize_direction("mixed") is None
    assert normalize_direction("risk_on") == "long"
