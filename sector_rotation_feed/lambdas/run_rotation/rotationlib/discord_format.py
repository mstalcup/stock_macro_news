from __future__ import annotations


def _fmt_pct(v: float | None, width: int = 6) -> str:
    if v is None:
        return "  n/a "
    sign = "+" if v > 0 else ""
    return f"{sign}{v:.{max(1, width - 4)}f}%".rjust(width)


def _heat_cell(v: float | None) -> str:
    if v is None:
        return "·"
    if v >= 1.0:
        return "🟩"
    if v >= 0.25:
        return "🟢"
    if v <= -1.0:
        return "🟥"
    if v <= -0.25:
        return "🔴"
    return "🟡"


def format_messages(report: dict) -> list[str]:
    td = report.get("trade_date", "?")
    spy = report.get("spy_returns") or {}
    lines: list[str] = [
        f"**Sector rotation — {td}** (vs SPY)",
        f"SPY: 1d {_fmt_pct(spy.get('1d'))} | 5d {_fmt_pct(spy.get('5d'))} | 20d {_fmt_pct(spy.get('20d'))}",
        "_Relative returns vs SPY. RS = sector/SPY ratio vs 20d MA. Not investment advice._",
        "",
        "**Heatmap** (`1d/5d/20d rel` + RS trend)",
        "```",
        f"{'ETF':<5} {'Sector':<12} {'1d':>7} {'5d':>7} {'20d':>7}  H",
    ]
    for s in report.get("sectors") or []:
        h = "".join(_heat_cell(s.get(k)) for k in ("rel_1d", "rel_5d", "rel_20d"))
        rs = "↑" if s.get("rs_above_ma") else "↓"
        lines.append(
            f"{s['ticker']:<5} {s['name'][:12]:<12} "
            f"{_fmt_pct(s.get('rel_1d'))} {_fmt_pct(s.get('rel_5d'))} {_fmt_pct(s.get('rel_20d'))}  {h}{rs}"
        )
    lines.append("```")

    in_s = report.get("in_sectors") or []
    out_s = report.get("out_sectors") or []
    lines.extend(["", "**IN** (RS above 20d MA + momentum)"])
    if in_s:
        for s in in_s:
            lines.append(f"• **{s['ticker']}** {s['name']} — score `{s['score']}`")
    else:
        lines.append("_No sectors met IN criteria today._")

    lines.extend(["", "**OUT** (weak RS / relative weakness)"])
    if out_s:
        for s in out_s:
            lines.append(f"• **{s['ticker']}** {s['name']} — score `{s['score']}`")
    else:
        lines.append("_No clear OUT signals._")

    for block in report.get("drill_down") or []:
        sec = block.get("sector", "?")
        lines.extend(["", f"**Drill-down — {sec}** (approx. top holdings)"])
        movers = block.get("top_movers_5d") or []
        if movers:
            lines.append("_Top 5d movers:_")
            for m in movers:
                lines.append(
                    f"  `{m['ticker']}` 5d {_fmt_pct(m.get('ret_5d'))} | 1d {_fmt_pct(m.get('ret_1d'))}"
                )
        vol = block.get("top_rel_volume") or []
        if vol:
            lines.append("_High relative volume (vs 20d avg):_")
            for v in vol:
                rv = v.get("rel_volume")
                rv_s = f"{rv:.1f}x" if rv is not None else "n/a"
                lines.append(f"  `{v['ticker']}` vol {rv_s} | 1d {_fmt_pct(v.get('ret_1d'))}")

    text = "\n".join(lines)
    return _chunk(text, 1900)


def _chunk(text: str, limit: int) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    if len(t) <= limit:
        return [t]
    out: list[str] = []
    while t:
        if len(t) <= limit:
            out.append(t)
            break
        cut = t.rfind("\n\n", 0, limit)
        if cut < limit // 3:
            cut = t.rfind("\n", 0, limit)
        if cut < limit // 3:
            cut = limit
        out.append(t[:cut].rstrip())
        t = t[cut:].lstrip()
    return out
