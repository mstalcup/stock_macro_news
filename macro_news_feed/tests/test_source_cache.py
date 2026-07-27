import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

FETCH = Path(__file__).resolve().parents[1] / "lambdas" / "fetch_news_slot"
sys.path.insert(0, str(FETCH))

from newslib.source_cache import load_cached_provider_articles  # noqa: E402


def test_load_cached_returns_none_when_empty_articles():
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {
        "Body": MagicMock(read=lambda: b'{"articles": [], "fetched_at": "t"}')
    }
    with patch("newslib.source_cache.boto3.client", return_value=mock_s3):
        assert (
            load_cached_provider_articles(
                bucket="b", issue_date="2026-05-15", slot="pre_open", provider="newsapi"
            )
            is None
        )


def test_load_cached_returns_articles_when_present():
    payload = b'{"articles": [{"title": "Fed"}], "fetched_at": "2026-05-15T12:00:00Z"}'
    mock_s3 = MagicMock()
    mock_s3.get_object.return_value = {"Body": MagicMock(read=lambda: payload)}
    with patch("newslib.source_cache.boto3.client", return_value=mock_s3):
        hit = load_cached_provider_articles(
            bucket="b", issue_date="2026-05-15", slot="pre_open", provider="newsapi"
        )
    assert hit is not None
    articles, meta = hit
    assert len(articles) == 1
    assert meta["article_count"] == 1
