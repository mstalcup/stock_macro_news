import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambdas" / "query_llm_panel"))

from sentimentlib.parse import normalize_picks  # noqa: E402


def test_normalize_picks_filters_bad_tickers():
    parsed = normalize_picks(
        {
            "market_bias": "risk_on",
            "picks": [
                {
                    "ticker": "NVDA",
                    "direction": "long",
                    "conviction": "high",
                    "themes": ["ai"],
                    "rationale": "chips",
                    "catalysts": "",
                },
                {
                    "ticker": "NASDAQ",
                    "direction": "long",
                    "conviction": "low",
                    "themes": [],
                    "rationale": "x",
                    "catalysts": "",
                },
            ],
        }
    )
    assert len(parsed["picks"]) == 1
    assert parsed["picks"][0]["ticker"] == "NVDA"
