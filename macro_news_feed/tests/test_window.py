from datetime import datetime, timezone
import sys
from pathlib import Path
from zoneinfo import ZoneInfo

FETCH = Path(__file__).resolve().parents[1] / "lambdas" / "fetch_news_slot"
sys.path.insert(0, str(FETCH))

from newslib.window import fetch_window, issue_window  # noqa: E402


def test_pre_open_is_full_calendar_day():
    ws, we = fetch_window("2026-05-15", "pre_open")
    ws0, we0 = issue_window("2026-05-15")
    assert ws == ws0 and we == we0


def test_pre_close_starts_at_morning_briefing_pt():
    ws, we = fetch_window("2026-05-15", "pre_close")
    pt = ZoneInfo("America/Los_Angeles")
    assert ws.astimezone(pt).hour == 6
    assert ws.astimezone(pt).minute == 25
    assert we.astimezone(pt).date().isoformat() == "2026-05-16"
