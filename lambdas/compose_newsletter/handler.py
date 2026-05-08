"""
lambdas/compose_newsletter/handler.py

Lambda 3 of 4 in the Market Pulse Step Function.

Reads raw_data + signals from DynamoDB, calls Claude to synthesize
a human-readable morning newsletter, then stores the result.

The newsletter has four sections:
  1. Market Regime Snapshot (one paragraph)
  2. Sector Rotation Signals (alerts + ranked table)
  3. Macro News Digest (top 5 stories synthesized by Claude)
  4. What to Watch (Claude's forward-looking commentary)
"""
import os
import json
import anthropic
from datetime import datetime, timezone

try:
    from shared.config import SK_RAW_DATA, SK_SIGNALS, SK_NEWSLETTER
    from shared.dynamo import save_report, load_report
except ImportError:
    import sys; sys.path.insert(0, os.path.join(os.path.dirname(__file__), "../.."))
    from shared.config import SK_RAW_DATA, SK_SIGNALS, SK_NEWSLETTER
    from shared.dynamo import save_report, load_report


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builders
# ─────────────────────────────────────────────────────────────────────────────

def _format_sector_table(ranked_sectors: list[dict]) -> str:
    """Format ranked sector data into a compact string for the prompt."""
    lines = ["SECTOR MOMENTUM RANKINGS (1d / 5d / 20d returns):"]
    for i, s in enumerate(ranked_sectors, 1):
        r1  = f"{s['return_1d']:+.2f}%" if s.get('return_1d') is not None else "N/A"
        r5  = f"{s['return_5d']:+.2f}%" if s.get('return_5d') is not None else "N/A"
        r20 = f"{s['return_20d']:+.2f}%" if s.get('return_20d') is not None else "N/A"
        score = f"{s['momentum_score']:+.2f}" if s.get('momentum_score') is not None else "N/A"
        lines.append(f"  #{i:2d}. {s['ticker']:5s} ({s['name']:<28s}) "
                     f"1d:{r1:>8s} | 5d:{r5:>8s} | 20d:{r20:>8s} | score:{score}")
    return "\n".join(lines)


def _format_rotation_alerts(rotation_signals: list[dict]) -> str:
    if not rotation_signals:
        return "No unusual sector rotation signals today."
    lines = ["ROTATION ALERTS (statistically significant sector moves):"]
    for sig in rotation_signals:
        lines.append(f"  ⚡ {sig['ticker']} ({sig['name']}): "
                     f"{sig['direction']} {sig['strength']} | "
                     f"1d return: {sig['return_1d']:+.2f}% | z-score: {sig['z_score']:+.2f}")
    return "\n".join(lines)


def _format_cluster_alerts(cluster_signals: list[dict]) -> str:
    if not cluster_signals:
        return "No correlated cluster moves detected."
    lines = ["CLUSTER SIGNALS (correlated sector groups moving together):"]
    for sig in cluster_signals:
        tickers = ", ".join(f"{m['ticker']}({m['return_1d']:+.1f}%)" for m in sig["movers"])
        lines.append(f"  📊 {sig['cluster']} cluster moving {sig['direction']}: "
                     f"{tickers} | avg: {sig['avg_move']:+.2f}%")
    return "\n".join(lines)


def _format_top_news(av_news: list[dict], newsapi: list[dict]) -> str:
    """Combine and format top news headlines for the prompt."""
    lines = ["TOP NEWS STORIES (last 24-48 hours):"]

    # Alpha Vantage news (has sentiment scores, prioritise high-impact)
    av_sorted = sorted(av_news, key=lambda a: abs(a.get("sentiment_score", 0)), reverse=True)
    for i, article in enumerate(av_sorted[:10], 1):
        sentiment = article.get("overall_sentiment", "")
        score = article.get("sentiment_score", 0)
        lines.append(f"\n  [{i}] [{sentiment} {score:+.2f}] {article['title']}")
        if article.get("summary"):
            lines.append(f"      Summary: {article['summary'][:300]}")
        if article.get("ticker_sentiment"):
            ts = article["ticker_sentiment"][:3]
            lines.append(f"      Tickers: {', '.join(t['ticker'] + '(' + t['sentiment_label'] + ')' for t in ts)}")

    # NewsAPI headlines (broader macro, not tied to specific stocks)
    lines.append("\nMACRO HEADLINES (NewsAPI):")
    for i, article in enumerate(newsapi[:10], 1):
        lines.append(f"  [{i}] [{article.get('source', '')}] {article['title']}")
        if article.get("description"):
            lines.append(f"      {article['description'][:250]}")

    return "\n".join(lines)


def _format_macro_snapshot(regime: dict) -> str:
    """Format market regime data."""
    vix_str = f"{regime['vix']:.1f}" if regime.get("vix") else "N/A"
    lines = [
        f"MARKET REGIME: {regime['regime']}",
        f"  VIX: {vix_str}",
        f"  SPY 1d: {regime['spy_1d']:+.2f}%" if regime.get("spy_1d") is not None else "  SPY: N/A",
        f"  TLT 1d: {regime['tlt_1d']:+.2f}%" if regime.get("tlt_1d") is not None else "  TLT: N/A",
        f"  GLD 1d: {regime['gld_1d']:+.2f}%" if regime.get("gld_1d") is not None else "  GLD: N/A",
        f"  USO 1d: {regime['uso_1d']:+.2f}%" if regime.get("uso_1d") is not None else "  USO: N/A",
        f"  BTC(IBIT) 1d: {regime['ibit_1d']:+.2f}%" if regime.get("ibit_1d") is not None else "  BTC: N/A",
    ]
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Claude composition
# ─────────────────────────────────────────────────────────────────────────────

SYSTEM_PROMPT = """You are a sharp, concise financial analyst writing a pre-market morning brief 
for a retail investor who wants to understand macro trends, sector rotation, and emerging opportunities.

Your writing style:
- Confident but not reckless. Signal uncertainty where it exists.
- Plain English. No jargon unless you explain it briefly.
- Specific and concrete. Say "Tech (+2.1%) is outpacing Utilities (-0.8%)" not "some sectors are up."
- Forward-looking. Connect today's data to potential near-term plays.
- Skeptical. Flag when a signal could be noise, not a real rotation.

Format the newsletter with these exact sections:
1. 🌅 MARKET SNAPSHOT (2-3 sentences: regime, VIX, biggest macro cross-asset moves)
2. 🔄 SECTOR ROTATION (explain what the signals mean, not just what they are. Is it noise or a real pattern? What's the narrative?)
3. 📰 MACRO NEWS DIGEST (synthesize the top 3-5 stories into a coherent narrative. What's the single biggest macro theme today?)
4. 🎯 WHAT TO WATCH (2-3 specific things to monitor today: a sector to watch, a data release, a geopolitical factor)

Keep total length under 600 words. Be direct. Start with section 1 immediately — no preamble."""


def compose_with_claude(raw: dict, signals: dict) -> str:
    """Call Claude to synthesize all data into the newsletter."""
    client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

    date_str = raw.get("date", "today")

    # Build the data context for Claude
    data_context = f"""
DATE: {date_str}

{_format_macro_snapshot(signals['market_regime'])}

{_format_sector_table(signals['ranked_sectors'])}

{_format_rotation_alerts(signals['rotation_signals'])}

{_format_cluster_alerts(signals['cluster_signals'])}

TOP MOVERS (all ETFs):
{chr(10).join(f"  {m['ticker']:6s}: {m['return_1d']:+.2f}%" for m in signals['top_movers'][:8])}

{_format_top_news(raw.get('av_news', []), raw.get('newsapi_headlines', []))}
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1024,
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": f"Here is today's market data. Write the morning brief:\n\n{data_context}"
            }
        ],
    )

    return message.content[0].text


# ─────────────────────────────────────────────────────────────────────────────
# Lambda handler
# ─────────────────────────────────────────────────────────────────────────────

def handler(event: dict, context) -> dict:
    date_str = event.get("date")
    print(f"[compose_newsletter] Running for date: {date_str}")

    # Load both upstream reports
    raw     = load_report(date_str, SK_RAW_DATA)
    signals = load_report(date_str, SK_SIGNALS)

    if not raw or not signals:
        raise ValueError(f"Missing raw_data or signals for {date_str}")

    # Generate newsletter
    print("[compose_newsletter] Calling Claude...")
    newsletter_text = compose_with_claude(raw, signals)

    # Store newsletter
    newsletter_payload = {
        "date":             date_str,
        "generated_at":     datetime.now(timezone.utc).isoformat(),
        "newsletter":       newsletter_text,
        "regime":           signals["market_regime"]["regime"],
        "rotation_alerts":  len(signals["rotation_signals"]),
        "cluster_alerts":   len(signals["cluster_signals"]),
        "word_count":       len(newsletter_text.split()),
    }

    save_report(date_str, SK_NEWSLETTER, newsletter_payload)
    print(f"[compose_newsletter] Newsletter saved ({newsletter_payload['word_count']} words)")

    return {
        "date":   date_str,
        "status": "ok",
        "regime": signals["market_regime"]["regime"],
        "preview": newsletter_text[:200] + "...",
    }
