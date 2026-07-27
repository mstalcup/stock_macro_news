"""Which issue-source rows are worth posting to Discord."""
from __future__ import annotations

from video_links import links_for_report

NO_UPDATES_GLOBAL = "No updates today."

_SKIP_MODES = frozenset({"no_update", "no_transcript", "unavailable"})


def is_actionable_issue_source(row: dict, *, fetch_row: dict | None = None) -> bool:
    """True when we have a real summary backed by transcript (for thread posts)."""
    if row.get("status") != "FETCHED":
        return False
    mode = (row.get("source_summary_mode") or "").strip().lower()
    if mode in _SKIP_MODES:
        return False
    summary = (row.get("source_summary") or "").strip()
    if not summary or summary.lower().startswith("no transcript"):
        return False
    if mode != "openai":
        return False
    links = links_for_report(fetch_row=fetch_row) if fetch_row else []
    if not links:
        links = [
            u
            for u in (row.get("source_links") or [])
            if isinstance(u, str) and u.startswith("http")
        ]
    return bool(links)
