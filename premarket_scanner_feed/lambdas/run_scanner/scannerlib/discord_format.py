from __future__ import annotations

from typing import Any


def format_gappers_messages(payload: dict[str, Any]) -> list[str]:
    gappers = payload.get("gappers") or []
    issue = payload.get("issue_date") or ""
    lines = [
        f"**Premarket Gappers — {issue}**",
        f"_{payload.get('scanned_at', '')}_",
        f"{len(gappers)} names | filters: gap>{payload.get('filters', {}).get('min_gap_pct', 5)}% "
        f"price>${payload.get('filters', {}).get('min_price', 3)} vol>"
        f"{int(payload.get('filters', {}).get('min_volume', 50000))}",
        "",
    ]
    if not gappers:
        lines.append("_No names passed filters._")
    for g in gappers:
        cat = g.get("catalyst") or "_no catalyst_"
        lines.append(
            f"**{g['rank']}. {g['symbol']}** +{g['gap_pct']}% @ ${g['price']} | vol {g['premarket_volume']:,}"
        )
        lines.append(f"  Catalyst: {cat}")
        for h in g.get("headlines") or []:
            lines.append(f"  • {h}")
        lines.append("")
    lines.append("_TJL confirmation runs ~10:05 AM ET._")
    return _chunk("\n".join(lines).strip(), 1900)


def format_tjl_messages(payload: dict[str, Any]) -> list[str]:
    if payload.get("error"):
        return [
            f"**Trend Join Long — skipped**\n{payload.get('message') or payload.get('error')}"
        ]
    hits = payload.get("hits") or []
    results = payload.get("all_results") or []
    n_pass = sum(1 for r in results if r.get("result") == "PASS")
    n_daily = sum(1 for r in results if r.get("result") == "fail_daily")
    n_intra = sum(1 for r in results if r.get("result") == "fail_intraday")
    lines = [
        f"**Trend Join Long — {payload.get('issue_date', '')}**",
        f"_{payload.get('scanned_at', '')}_",
        f"Checked: {payload.get('candidates_checked', 0)} | PASS: {n_pass} | "
        f"fail_daily: {n_daily} | fail_intraday: {n_intra}",
        "",
    ]
    for h in hits:
        lines.append(
            f"✅ **{h['symbol']}** ${h.get('curr_price')} — "
            f"prev_high {h.get('prev_daily_high')} | SMA200 {h.get('sma200')} | "
            f"PMH {h.get('pmh')} | HOD {h.get('today_hod')}"
        )
    if hits:
        lines.append("")
    for r in results:
        if r.get("result") == "PASS":
            continue
        reason = r.get("reason") or ""
        if len(reason) > 120:
            reason = reason[:117] + "..."
        lines.append(f"❌ **{r['symbol']}** — {r.get('result')} ({reason})")
    if not results:
        lines.append("_No candidates (empty gappers universe)._")
    return _chunk("\n".join(lines).strip(), 1900)


def _chunk(text: str, max_len: int) -> list[str]:
    if len(text) <= max_len:
        return [text]
    parts: list[str] = []
    rest = text
    while len(rest) > max_len:
        cut = rest.rfind("\n", 0, max_len)
        if cut < max_len // 2:
            cut = max_len
        parts.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest:
        parts.append(rest)
    return parts
