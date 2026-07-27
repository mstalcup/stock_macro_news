"""Run: py -m unittest macro_news_feed.tests.test_dedupe  OR  cd macro_news_feed && py -m unittest tests.test_dedupe"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "lambdas" / "fetch_news_slot"))

from newslib.dedupe import dedupe_articles  # noqa: E402


class TestDedupe(unittest.TestCase):
    def test_url_overlap_merges_providers(self):
        url = "https://example.com/story/1"
        by_provider = {
            "alpha_vantage": [
                {
                    "article_id": "a",
                    "url": url,
                    "canonical_url": url,
                    "title": "Fed holds rates",
                    "published_at": "2026-05-15T10:00:00+00:00",
                    "summary": "av",
                    "source_name": "Reuters",
                    "sentiment_score": 0.2,
                }
            ],
            "newsapi": [
                {
                    "article_id": "b",
                    "url": url + "?utm_source=x",
                    "canonical_url": url,
                    "title": "Fed holds rates steady",
                    "published_at": "2026-05-15T09:00:00+00:00",
                    "summary": "na",
                    "source_name": "CNBC",
                }
            ],
            "finnhub": [],
        }
        deduped, stats = dedupe_articles(by_provider)
        self.assertEqual(stats["deduped_count"], 1)
        self.assertEqual(len(deduped[0]["providers"]), 2)


if __name__ == "__main__":
    unittest.main()
