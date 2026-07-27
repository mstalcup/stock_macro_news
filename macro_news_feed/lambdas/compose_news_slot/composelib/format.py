from __future__ import annotations


def format_digest_markdown(
    *,
    issue_date: str,
    slot: str,
    slot_label: str,
    digest: dict,
) -> str:
    bias = (digest.get("market_bias") or "mixed").replace("_", " ").title()
    lines = [
        f"**Macro digest — {issue_date} ({slot_label})**",
        f"**Bias:** {bias}",
        "",
        (digest.get("executive_summary") or "").strip(),
        "",
    ]

    themes = digest.get("dominant_themes") or []
    if themes:
        lines.append("**Themes**")
        for t in themes[:8]:
            if not isinstance(t, dict):
                continue
            theme = (t.get("theme") or "").strip()
            sent = (t.get("sentiment") or "").strip()
            why = (t.get("why_it_matters") or "").strip()
            lines.append(f"- **{theme}** ({sent}): {why}")
        lines.append("")

    catalysts = (digest.get("catalysts_ahead") or "").strip()
    if catalysts:
        lines.append("**Catalysts**")
        lines.append(catalysts)
        lines.append("")

    assets = digest.get("asset_class_notes") or {}
    if isinstance(assets, dict) and any((assets.get(k) or "").strip() for k in assets):
        lines.append("**Asset classes**")
        for label, key in (
            ("Equities", "equities"),
            ("Rates / FX", "rates_fx"),
            ("Commodities", "commodities"),
            ("Crypto", "crypto"),
        ):
            note = (assets.get(key) or "").strip()
            if note:
                lines.append(f"- {label}: {note}")
        lines.append("")

    tickers = digest.get("ticker_watchlist") or []
    if tickers:
        lines.append("**Ticker watch**")
        for t in tickers[:12]:
            if not isinstance(t, dict):
                continue
            sym = (t.get("ticker") or "").strip()
            bias_t = (t.get("bias") or "").strip()
            note = (t.get("note") or "").strip()
            lines.append(f"- **{sym}** ({bias_t}): {note}")
        lines.append("")

    risks = (digest.get("risks_to_watch") or "").strip()
    if risks:
        lines.append("**Risks**")
        lines.append(risks)
        lines.append("")

    shift = (digest.get("vs_prior_slot") or "").strip()
    if shift:
        lines.append(f"**Vs prior slot:** {shift}")
        lines.append("")

    takeaways = digest.get("actionable_takeaways") or []
    if takeaways:
        lines.append("**Actionable**")
        for item in takeaways[:6]:
            if isinstance(item, str) and item.strip():
                lines.append(f"- {item.strip()}")
        lines.append("")

    return "\n".join(lines).strip()
