"""Render signal matrix for terminal / markdown."""
from __future__ import annotations

from .normalize import direction_arrow
from .types import ConfluenceTier, SignalMatrix, TickerMatrixRow


def _cell_display(row: TickerMatrixRow, source_id: str) -> str:
    c = row.cells.get(source_id)
    if not c:
        return "·"
    if c.kind == "holding":
        return f"H{direction_arrow(c.direction)}"
    arrow = direction_arrow(c.direction)
    if c.label and c.direction is None:
        return f"·({c.label[:4]})"
    return arrow if c.direction else "·"


def format_markdown(matrix: SignalMatrix, *, max_rows: int = 25) -> str:
    source_ids: list[str] = []
    for row in matrix.rows:
        for sid in row.cells:
            if sid not in source_ids:
                source_ids.append(sid)
    source_ids.sort(key=lambda s: (s.split(":")[0], s))

    lines = [
        f"# Signal matrix — {matrix.issue_date} ({matrix.slot})",
        "",
        "| Ticker | Confluence | " + " | ".join(source_ids[:12]) + " |",
        "|--------|------------|" + "|".join(["---"] * min(len(source_ids), 12)) + "|",
    ]

    shown = 0
    for row in matrix.rows:
        if shown >= max_rows:
            break
        conf = row.confluence
        tier = conf.tier.value if conf else "none"
        badge = f"{tier} ({conf.score:.2f})" if conf else "none"
        cells = [_cell_display(row, sid) for sid in source_ids[:12]]
        lines.append(f"| **{row.ticker}** | {badge} | " + " | ".join(cells) + " |")
        shown += 1

    high = [
        r
        for r in matrix.rows
        if r.confluence
        and r.confluence.tier in (ConfluenceTier.UNANIMOUS, ConfluenceTier.STRONG)
        and r.confluence.long_channels + r.confluence.short_channels > 0
    ]
    if high:
        lines.extend(["", "## High confluence", ""])
        for row in high[:15]:
            conf = row.confluence
            side = "LONG" if conf.long_channels >= conf.short_channels else "SHORT"
            ch = ", ".join(
                f"{cid}:{direction_arrow(ch.trade_direction)}"
                for cid, ch in sorted(row.channels.items())
                if ch.trade_direction and cid != "hedge_fund"
            )
            lines.append(f"- **{row.ticker}** {side} — {conf.tier.value} ({ch})")
            if conf.holding_note:
                lines.append(f"  - _Holdings:_ {conf.holding_note}")

    lines.append("")
    lines.append("_Legend: L=long, S=short, ·=neutral/abstain, H=holding (fund positions)_")
    return "\n".join(lines)


def format_console(matrix: SignalMatrix, *, max_rows: int = 30) -> str:
    lines = [
        f"Signal matrix {matrix.issue_date} slot={matrix.slot}",
        "",
    ]
    for pr in matrix.provider_results:
        err = f" errors={pr.errors}" if pr.errors else ""
        lines.append(f"  [{pr.provider_id}] votes={len(pr.votes)}{err}")

    lines.append("")
    lines.append(f"{'TICKER':8} {'TIER':10} {'SCORE':6} {'LONG/SHORT CH':14} SOURCES")
    for row in matrix.rows[:max_rows]:
        conf = row.confluence
        if not conf:
            continue
        side = f"{conf.long_channels}L/{conf.short_channels}S"
        sources = " ".join(
            f"{sid.split(':')[-1][:8]}:{_cell_display(row, sid)}"
            for sid in sorted(row.cells.keys())
        )
        lines.append(
            f"{row.ticker:8} {conf.tier.value:10} {conf.score:6.2f} {side:14} {sources}"
        )

    strong = [r for r in matrix.rows if r.confluence and r.confluence.tier.value in ("unanimous", "strong")]
    if strong:
        lines.extend(["", "High confluence:"])
        for row in strong[:12]:
            conf = row.confluence
            lines.append(
                f"  {row.ticker:8} {conf.tier.value} "
                f"({conf.long_channels} long ch / {conf.short_channels} short ch)"
            )

    return "\n".join(lines)
