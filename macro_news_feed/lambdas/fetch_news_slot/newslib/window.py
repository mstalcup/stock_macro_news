"""Issue-date window helpers (Pacific calendar day by default)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo


def issue_window(
    issue_date: str,
    tz_name: str = "America/Los_Angeles",
) -> tuple[datetime, datetime]:
    """
    Inclusive start, exclusive end in UTC for one local calendar day.
    issue_date=2026-05-15 → 2026-05-15 00:00 PT .. 2026-05-16 00:00 PT.
    """
    tz = ZoneInfo(tz_name)
    start_local = datetime.fromisoformat(f"{issue_date}T00:00:00").replace(tzinfo=tz)
    end_local = start_local + timedelta(days=1)
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def fetch_window(
    issue_date: str,
    slot: str,
    tz_name: str = "America/Los_Angeles",
    *,
    pre_open_local_time: str = "06:25:00",
) -> tuple[datetime, datetime]:
    """
    Time window for headline fetch/filtering.

    - pre_open: full Pacific calendar day (overnight + morning wires).
    - pre_close: from pre_open schedule time through end of day (afternoon recap).
    """
    if slot == "pre_close":
        tz = ZoneInfo(tz_name)
        start_local = datetime.fromisoformat(f"{issue_date}T{pre_open_local_time}").replace(
            tzinfo=tz
        )
        end_local = datetime.fromisoformat(f"{issue_date}T00:00:00").replace(tzinfo=tz) + timedelta(
            days=1
        )
        return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)
    return issue_window(issue_date, tz_name)


def window_iso_utc_z(window_start: datetime, window_end: datetime) -> tuple[str, str]:
    """NewsAPI /everything `from` / `to` parameters (UTC Z)."""
    ws = window_start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    we = window_end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return ws, we


def parse_published_utc(raw: str) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def published_in_window(
    published_at: str,
    window_start: datetime,
    window_end: datetime,
) -> bool:
    """True if published_at falls in [window_start, window_end)."""
    pub = parse_published_utc(published_at)
    if pub is None:
        return False
    return window_start <= pub < window_end


def av_time_format(dt: datetime) -> str:
    """Alpha Vantage: YYYYMMDDTHHMM (UTC)."""
    u = dt.astimezone(timezone.utc)
    return u.strftime("%Y%m%dT%H%M")
