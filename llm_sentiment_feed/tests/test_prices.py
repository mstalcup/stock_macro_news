import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambdas" / "score_recommendations"))

from scorerlib.prices import calendar_add, normalize_symbol  # noqa: E402


def test_normalize_symbol_equity():
    assert normalize_symbol("nvda") == "NVDA"


def test_normalize_symbol_crypto_fx():
    assert normalize_symbol("BTC") == "BTC-USD"
    assert normalize_symbol("USDJPY") == "JPY=X"
    assert normalize_symbol("GBPUSD") == "GBPUSD=X"


def test_calendar_add():
    assert calendar_add("2026-05-15", 7) == "2026-05-22"
