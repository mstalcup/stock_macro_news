"""Inspect raw Alpha Vantage NEWS_SENTIMENT (topic + RELEVANCE, issue-day window)."""
from __future__ import annotations

import os
import sys
from pathlib import Path

FEED = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(FEED / "lambdas" / "fetch_news_slot"))
for line in (FEED / ".env").read_text().splitlines():
    if "=" in line and not line.strip().startswith("#"):
        k, _, v = line.partition("=")
        os.environ[k.strip()] = v.strip().strip('"').strip("'")

from newslib.config import AV_MACRO_TOPIC
from newslib.fetchers.alpha_vantage import _request_feed
from newslib.window import av_time_format, issue_window

issue_date = sys.argv[1] if len(sys.argv) > 1 else "2026-05-15"
ws, we = issue_window(issue_date)
key = os.environ.get("ALPHA_VANTAGE_API_KEY", "")
data, err = _request_feed(
    key,
    topic=AV_MACRO_TOPIC,
    time_from=av_time_format(ws),
    time_to=av_time_format(we),
)
print("issue_date", issue_date, "topic", AV_MACRO_TOPIC, "sort=RELEVANCE")
print("window", ws.isoformat(), "->", we.isoformat())
print("error:", err)
feed = data.get("feed") or []
print("feed count:", len(feed))
for i, a in enumerate(feed[:12], 1):
    print(f"{i:2}. {a.get('time_published', '')[:15]} | {a.get('title', '')[:95]}")
