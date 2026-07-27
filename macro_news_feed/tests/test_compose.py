import sys
from pathlib import Path

COMPOSE = Path(__file__).resolve().parents[1] / "lambdas" / "compose_news_slot"
sys.path.insert(0, str(COMPOSE))

from composelib.format import format_digest_markdown  # noqa: E402
from composelib.heuristic import build_heuristic_digest  # noqa: E402
from composelib.llm import build_headline_blocks, normalize_digest  # noqa: E402


def test_build_headline_blocks_truncates_summary():
    articles = [
        {
            "article_id": "abc",
            "title": "Oil jumps",
            "summary": "x" * 500,
            "providers": ["newsapi"],
            "tickers": ["XOM"],
        }
    ]
    blocks = build_headline_blocks(articles)
    assert len(blocks) == 1
    assert "id=abc" in blocks[0]
    assert len(blocks[0]) < 700


def test_heuristic_digest_groups_themes():
    articles = [
        {
            "title": "Fed holds rates steady",
            "summary": "FOMC meeting",
            "providers": ["newsapi"],
            "tickers": [],
        },
        {
            "title": "Oil rises on Iran tensions",
            "summary": "crude up",
            "providers": ["finnhub"],
            "tickers": [],
        },
    ]
    digest = build_heuristic_digest(
        articles=articles, issue_date="2026-05-15", slot="pre_open", prior_text=""
    )
    assert digest["market_bias"] == "mixed"
    assert len(digest["dominant_themes"]) >= 1


def test_format_digest_markdown_includes_bias():
    digest = normalize_digest(
        {
            "market_bias": "risk_off",
            "executive_summary": "Risk-off day.",
            "dominant_themes": [],
            "catalysts_ahead": "",
            "asset_class_notes": {
                "equities": "",
                "rates_fx": "",
                "commodities": "",
                "crypto": "",
            },
            "ticker_watchlist": [],
            "risks_to_watch": "",
            "vs_prior_slot": "",
            "actionable_takeaways": ["Reduce beta."],
        }
    )
    md = format_digest_markdown(
        issue_date="2026-05-15",
        slot="pre_open",
        slot_label="Pre-open",
        digest=digest,
    )
    assert "Risk Off" in md or "risk" in md.lower()
    assert "Reduce beta" in md
