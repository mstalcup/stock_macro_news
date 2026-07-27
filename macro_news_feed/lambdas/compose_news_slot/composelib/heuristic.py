from __future__ import annotations

import re

THEME_KEYWORDS: list[tuple[str, list[str]]] = [
    ("Fed / rates", ["fed", "rate", "powell", "fomc", "treasury yield", "bond"]),
    ("Inflation / data", ["cpi", "inflation", "ppi", "jobs", "payroll", "gdp"]),
    ("Geopolitics", ["iran", "israel", "ukraine", "war", "sanction", "tariff", "china trade"]),
    ("Oil / energy", ["oil", "opec", "crude", "gasoline", "energy"]),
    ("Mega-cap tech", ["nvidia", "apple", "microsoft", "amazon", "meta", "alphabet", "ai chip"]),
    ("Crypto", ["bitcoin", "btc", "ethereum", "crypto"]),
    ("Earnings / corporate", ["earnings", "guidance", "merger", "acquisition", "ipo"]),
]


def _match_theme(title: str, summary: str) -> str | None:
    blob = f"{title} {summary}".lower()
    for theme, keys in THEME_KEYWORDS:
        if any(k in blob for k in keys):
            return theme
    return None


def build_heuristic_digest(
    *,
    articles: list[dict],
    issue_date: str,
    slot: str,
    prior_text: str,
) -> dict:
    if not articles:
        return {
            "market_bias": "unclear",
            "executive_summary": f"No headlines ingested for {issue_date} ({slot}).",
            "dominant_themes": [],
            "catalysts_ahead": "",
            "asset_class_notes": {
                "equities": "",
                "rates_fx": "",
                "commodities": "",
                "crypto": "",
            },
            "ticker_watchlist": [],
            "risks_to_watch": "",
            "vs_prior_slot": "",
            "actionable_takeaways": ["Re-run fetch when providers return articles."],
        }

    theme_buckets: dict[str, list[str]] = {}
    for art in articles[:25]:
        title = (art.get("title") or "").strip()
        summary = (art.get("summary") or "").strip()
        theme = _match_theme(title, summary) or "General market"
        theme_buckets.setdefault(theme, []).append(title)

    themes = []
    for theme, titles in sorted(theme_buckets.items(), key=lambda x: -len(x[1]))[:6]:
        themes.append(
            {
                "theme": theme,
                "sentiment": "mixed",
                "why_it_matters": "; ".join(titles[:3]),
                "headline_refs": titles[:3],
            }
        )

    top_titles = [a.get("title", "") for a in articles[:8] if a.get("title")]
    summary = (
        f"Headline scan for {issue_date} ({slot}) — {len(articles)} articles from "
        f"{', '.join(sorted({p for a in articles for p in (a.get('providers') or [])})) or 'feeds'}. "
        "Top stories: " + " | ".join(top_titles[:5])
    )
    if len(summary) > 900:
        summary = summary[:897] + "..."

    shift = ""
    if prior_text:
        shift = "Prior digest available; heuristic mode cannot compare nuance — review manually."

    tickers = []
    seen: set[str] = set()
    for art in articles:
        for t in art.get("tickers") or []:
            sym = re.sub(r"[^A-Z]", "", (t or "").upper())[:6]
            if sym and sym not in seen and len(sym) <= 5:
                seen.add(sym)
                tickers.append({"ticker": sym, "bias": "mixed", "note": (art.get("title") or "")[:120]})
            if len(tickers) >= 8:
                break

    return {
        "market_bias": "mixed",
        "executive_summary": summary,
        "dominant_themes": themes,
        "catalysts_ahead": "",
        "asset_class_notes": {
            "equities": themes[0]["why_it_matters"] if themes else "",
            "rates_fx": "",
            "commodities": "",
            "crypto": "",
        },
        "ticker_watchlist": tickers,
        "risks_to_watch": "Heuristic digest — enable OpenAI for catalysts and trade framing.",
        "vs_prior_slot": shift,
        "actionable_takeaways": [
            "Treat as headline list until LLM compose is enabled.",
            "Cross-check multi-source stories (providers[] length > 1).",
        ],
    }
