"""SA public-intel scan — statements, news, 13F pivot timeline for chip shorts."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whalelib.backtest import save_json
from whalelib.fund_13f_study import study_fund_13f_history

OUT = ROOT / "output"
SA_CIK = "2045724"

# Curated public sources (inferential + hard dates). Not exhaustive.
PUBLIC_SOURCES = [
    {
        "date": "2024-06-04",
        "type": "essay",
        "title": "Situational Awareness: The Decade Ahead",
        "url": "https://situational-awareness.ai/",
        "signal_strength": "inferential",
        "summary": (
            "165-page manifesto. Core investable thesis: AGI by ~2027, but the binding "
            "industrial constraint is **power/electricity and datacenter mobilization**, not "
            "chip design alone. Mentions CoWoS/HBM as near-term bottlenecks but argues chips "
            "scale faster than power. Does **not** say 'short NVDA' — supports long physical "
            "infra over pure silicon hype."
        ),
    },
    {
        "date": "2024-06-04",
        "type": "interview",
        "title": "Dwarkesh Podcast — 2027 AGI, launching AGI hedge fund",
        "url": "https://www.youtube.com/watch?v=zdbVtZIn9IM",
        "signal_strength": "inferential",
        "summary": (
            "4.5h interview tied to essay launch. Discusses trillion-dollar cluster, "
            "intelligence explosion, and starting an investment firm. Focus on compute "
            "buildout and geopolitics — no disclosed short-semiconductor book."
        ),
    },
    {
        "date": "2025-10-08",
        "type": "news",
        "title": "Fortune — fund at $1.5B+, thesis from manifesto",
        "url": "https://fortune.com/2025/10/08/leopold-aschenbrenner-openai-ftx-1-5-billion-hedge-fund-situational-awareness/",
        "signal_strength": "context",
        "summary": "Profiles fund growth and Silicon Valley backing. Physical AI infra narrative; no chip-short disclosure.",
    },
    {
        "date": "2026-02-11",
        "type": "13F",
        "title": "Q4 2025 13F-HR (filed Feb 2026)",
        "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=2045724",
        "signal_strength": "contradicts_chip_short",
        "summary": (
            "Public filing still **bullish chips via calls**: Intel calls ~$747M, CoreWeave "
            "calls ~$774M. Put notional tiny (~$10M). Would NOT have predicted Q1 chip short."
        ),
    },
    {
        "date": "2026-05-18",
        "type": "13F",
        "title": "Q1 2026 13F-HR — first chip put stack",
        "url": "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=2045724",
        "signal_strength": "hard",
        "summary": (
            "First public evidence of ~$8.46B put notional: SMH, NVDA, ORCL, AVGO, AMD, MU, "
            "TSM, ASML, INTC puts. Intel calls from prior quarter gone. **45-day lag** after "
            "quarter-end (Mar 31)."
        ),
    },
    {
        "date": "2026-05-19",
        "type": "news",
        "title": "Press coverage of Q1 13F (Yahoo, Dave Manuel, etc.)",
        "url": "https://finance.yahoo.com/markets/options/articles/leopold-aschenbrenners-situational-awareness-files-174339413.html",
        "signal_strength": "post-filing",
        "summary": "Media explains barbell: long power/miners/hosting, puts on 'priced for perfection' semis. All after 13F public.",
    },
]


def _intel_from_13f(study: dict) -> dict:
    quarters = study.get("quarters") or []
    timeline = []
    for q in quarters:
        calls = [h for h in q.get("all_holdings", []) if h.get("side") == "call"]
        puts = [h for h in q.get("all_holdings", []) if h.get("side") == "put"]
        chip_kw = ("NVIDIA", "INTEL", "ADVANCED MICRO", "BROADCOM", "MICRON", "ASML", "SEMICONDUCT", "TAIWAN")
        chip_puts = [p for p in puts if any(k in (p.get("issuer_name") or "").upper() for k in chip_kw)]
        chip_calls = [c for c in calls if any(k in (c.get("issuer_name") or "").upper() for k in chip_kw)]
        timeline.append(
            {
                "filing_date": q.get("filing_date"),
                "put_notional_usd": q.get("put_notional_usd"),
                "chip_put_notional_usd": round(sum(p["value_usd"] for p in chip_puts), 2),
                "chip_call_notional_usd": round(sum(c["value_usd"] for c in chip_calls), 2),
                "top_chip_puts": chip_puts[:8],
                "top_chip_calls": chip_calls[:8],
            }
        )
    diff = (study.get("quarter_diffs") or [{}])[0]
    return {"timeline": timeline, "latest_diff": diff}


def _write_report(path: Path, payload: dict) -> None:
    intel = payload["f13_intel"]
    lines = [
        "# Situational Awareness — public intel on chip shorts",
        "",
        "## Bottom line",
        "",
        payload["verdict"],
        "",
        "## What you could have known *before* the May 2026 13F",
        "",
    ]
    for s in PUBLIC_SOURCES:
        if s["signal_strength"] != "hard":
            lines.append(f"- **{s['date']}** [{s['type']}] {s['title']} ({s['signal_strength']})")
            lines.append(f"  - {s['summary']}")
            lines.append(f"  - {s['url']}")
            lines.append("")

    lines.extend(["## 13F pivot timeline (from SEC filings)", ""])
    for row in intel["timeline"]:
        lines.append(f"### Filed {row['filing_date']}")
        lines.append(
            f"- Chip puts: ${row['chip_put_notional_usd']/1e9:.2f}B | "
            f"Chip calls: ${row['chip_call_notional_usd']/1e9:.2f}B"
        )
        if row["top_chip_puts"]:
            lines.append("- Puts: " + ", ".join(
                f"{p.get('issuer_name','')[:20]} (${p['value_usd']/1e6:.0f}M)"
                for p in row["top_chip_puts"][:6]
            ))
        if row["top_chip_calls"]:
            lines.append("- Calls: " + ", ".join(
                f"{c.get('issuer_name','')[:20]} (${c['value_usd']/1e6:.0f}M)"
                for c in row["top_chip_calls"][:6]
            ))
        lines.append("")

    d = intel.get("latest_diff") or {}
    if d:
        lines.append("## Q4 → Q1 change (first public chip-short signal)")
        lines.append(f"- Put stack change: ${d.get('put_notional_change_usd', 0)/1e9:+.2f}B")
        if d.get("new_puts"):
            lines.append("- New puts included: " + ", ".join(
                f"{x.get('issuer_name','')[:18]}" for x in d["new_puts"][:10]
            ))

    lines.extend([
        "",
        "## Actionable monitor list (going forward)",
        "",
        "1. **13F put/call diffs** on SA CIK — only hard public signal for book pivots.",
        "2. **Essay/podcast thesis** — watch infra longs (power, hosting, memory); treat as context not short trigger.",
        "3. **13G/13D** — rare for SA (NBIS, CORZ only); volume confirm on those names.",
        "4. **News after 13F** — useful for narrative, not anticipatory.",
    ])
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-report", action="store_true")
    args = ap.parse_args()

    study = study_fund_13f_history(SA_CIK, fund_name="Situational Awareness LP", max_filings=4)
    intel = _intel_from_13f(study)

    verdict = (
        "**No clean public 'short chips' signal before the May 18, 2026 13F.** The June 2024 "
        "essay/interviews support a **long physical-infra / power** barbell thesis, but the "
        "prior 13F (Feb 2026) still showed **large Intel calls** — bullish chips. The ~$8.5B "
        "semiconductor put stack appeared suddenly in Q1 2026 and was only knowable from "
        "13F (or leaks) with ~45-day lag. Best forward monitor: **13F put diff** on CIK "
        "2045724, not 13D/G."
    )

    payload = {
        "fund_cik": SA_CIK,
        "verdict": verdict,
        "public_sources": PUBLIC_SOURCES,
        "f13_intel": intel,
        "f13_study": study,
    }

    OUT.mkdir(parents=True, exist_ok=True)
    out_json = OUT / "sa_public_intel.json"
    save_json(out_json, payload)
    print(f"Wrote {out_json}")

    if args.write_report:
        out_md = OUT / "sa_public_intel_report.md"
        _write_report(out_md, payload)
        print(f"Wrote {out_md}")

    print(verdict[:200] + "...")


if __name__ == "__main__":
    main()
