"""Backward case study for a single fund — what could have fired before 13F."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whalelib.backtest import save_json
from whalelib.case_study import build_fund_case_study, enrich_hits_for_ciks
from whalelib.fund_13f_study import study_fund_13f_history

OUT = ROOT / "output"
ROSTER = ROOT / "seed" / "fund_roster_curated.json"

# Situational Awareness LP + affiliated Partners entity
SA_DEFAULT_CIKS = ["2045724", "2038540"]


def _med_alpha(scored: list[dict], horizon: int = 20) -> dict:
    key = f"alpha_{horizon}d"
    vals = [r[key] for r in scored if r.get(key) is not None]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "med": round(statistics.median(vals), 4),
        "hit": round(sum(1 for v in vals if v > 0) / len(vals), 4),
    }


def _write_report(path: Path, study: dict) -> None:
    lines = [
        f"# Fund case study: {study['fund_name']}",
        "",
        f"CIKs: {', '.join(study['fund_ciks'])}",
        f"Schedule filings: {study['filing_rows']} | Unique tickers: {study['unique_tickers']}",
        "",
        "## Hypothesis buckets",
        "",
    ]
    for k, n in study["hypothesis_buckets"].items():
        lines.append(f"- **{k}**: {n} tickers")
    lines.extend(["", "## Positions (backward scan)", ""])
    for p in study["positions"]:
        lines.append(f"### {p['ticker']}")
        lines.append(f"- First filing: `{p['first_filing_type']}` on {p['first_filing_date']}")
        if p.get("volume_spike"):
            v = p["volume_spike"]
            lines.append(
                f"- Volume spike: {v['signal_date']} ({v['ratio']}x median, "
                f"{p.get('volume_lead_days')}d before filing)"
            )
        else:
            lines.append("- Volume spike: none in lookback")
        if p.get("eight_k"):
            ek = p["eight_k"]
            lines.append(f"- Issuer 8-K: {ek['signal_date']} ({ek['lead_days']}d before schedule)")
        else:
            lines.append("- Issuer 8-K: none")
        if p.get("earliest_detectable"):
            lines.append(
                f"- **Earliest detectable:** {p['earliest_detectable']} "
                f"via `{p['earliest_detectable_type']}`"
            )
        lines.append("")

    early = _med_alpha(study["scored_early_signals"])
    sched = _med_alpha(study["scored_schedule_signals"])
    early5 = _med_alpha(study["scored_early_signals"], 5)
    sched5 = _med_alpha(study["scored_schedule_signals"], 5)
    lines.extend(
        [
            "## Alpha comparison",
            "",
            f"| Track | n@5d | med α5d | n@20d | med α20d |",
            f"|-------|------|---------|-------|----------|",
            f"| Earliest detectable | {early5.get('n',0)} | {early5.get('med','—')}% | "
            f"{early.get('n',0)} | {early.get('med','—')}% |",
            f"| First schedule filing | {sched5.get('n',0)} | {sched5.get('med','—')}% | "
            f"{sched.get('n',0)} | {sched.get('med','—')}% |",
            "",
            "## Takeaway",
            "",
        ]
    )
    g_first = study["hypothesis_buckets"].get("13g_first", 0)
    if g_first:
        lines.append(
            f"- {g_first} ticker(s) entered via **13G first** (passive lane) — "
            "watch `13g_new` / `13g_increase`, not only 13D."
        )
    if study["hypothesis_buckets"].get("volume_early", 0):
        lines.append("- Volume spikes preceded schedule filing on some names — viable early layer.")
    if study["hypothesis_buckets"].get("8k_before_schedule", 0):
        lines.append("- Issuer 8-K mentioning fund fired before schedule on some names.")
    if not any(study["hypothesis_buckets"].get(k, 0) for k in ("volume_early", "8k_before_schedule")):
        lines.append("- No consistent pre-filing layer yet; schedule filing may be first public signal.")

    f13 = study.get("f13_history")
    if f13:
        lines.extend(["", "## 13F book (puts / longs — where chip shorts appear)", ""])
        lines.append(f"- {f13.get('schedule_filing_note', '')}")
        if f13.get("quarters"):
            q0 = f13["quarters"][0]
            lines.append(
                f"- Latest 13F ({q0['filing_date']}): {q0['holdings_count']} lines, "
                f"${q0['total_value_usd']/1e9:.2f}B total, "
                f"${q0['put_notional_usd']/1e9:.2f}B puts ({q0['put_pct_of_book']:.0f}% of book)"
            )
            lines.append("")
            lines.append("**Top chip puts (latest quarter):**")
            for p in f13.get("latest_chip_puts", [])[:12]:
                t = p.get("ticker") or p.get("issuer_name", "")[:20]
                lines.append(f"- {t}: ${p['value_usd']/1e6:.0f}M notional")
            if f13.get("quarter_diffs"):
                d = f13["quarter_diffs"][0]
                lines.append("")
                lines.append(
                    f"**QoQ put stack change** ({d['from_filing']} → {d['to_filing']}): "
                    f"${d['put_notional_change_usd']/1e9:+.2f}B"
                )
                if d.get("new_puts"):
                    lines.append("- New puts: " + ", ".join(
                        f"{x.get('issuer_name','')[:18]} (${x['value_usd']/1e6:.0f}M)"
                        for x in d["new_puts"][:8]
                    ))
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fund", default="Situational Awareness LP")
    ap.add_argument("--ciks", default=",".join(SA_DEFAULT_CIKS), help="Comma-separated fund CIKs")
    ap.add_argument("--years", type=float, default=2)
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--deep-13f", action="store_true", help="Include 13F puts/long history (chip shorts live here)")
    args = ap.parse_args()

    ciks = [c.strip() for c in args.ciks.split(",") if c.strip()]
    funds_meta = json.loads(ROSTER.read_text(encoding="utf-8")) if ROSTER.is_file() else []
    fund_row = next(
        (f for f in funds_meta if str(f.get("cik", "")).lstrip("0") in {c.lstrip("0") for c in ciks}),
        {"cik": ciks[0], "fund_name": args.fund},
    )

    print(f"Fetching schedule filings for {args.fund} ({', '.join(ciks)})...")
    hits = enrich_hits_for_ciks(ciks, years=int(args.years))
    print(f"  {len(hits)} raw filings")

    study = build_fund_case_study(
        fund_ciks=ciks,
        fund_name=args.fund,
        schedule_hits=hits,
        funds_meta=funds_meta or [fund_row],
    )
    if args.deep_13f:
        primary_cik = ciks[0]
        study["f13_history"] = study_fund_13f_history(
            primary_cik, fund_name=args.fund, max_filings=4
        )

    slug = args.fund.lower().replace(" ", "_")[:24]
    OUT.mkdir(parents=True, exist_ok=True)
    out_json = OUT / f"{slug}_case_study.json"
    save_json(out_json, study)
    print(f"Wrote {out_json}")

    if args.write_report:
        out_md = OUT / f"{slug}_case_study_report.md"
        _write_report(out_md, study)
        print(f"Wrote {out_md}")

    for p in study["positions"]:
        ed = p.get("earliest_detectable") or "—"
        print(
            f"  {p['ticker']:<8} first={p['first_filing_type']:<14} "
            f"filing={p['first_filing_date']} earliest={ed}"
        )
    if study.get("f13_history", {}).get("latest_chip_puts"):
        print("  --- 13F chip puts (latest) ---")
        for p in study["f13_history"]["latest_chip_puts"][:10]:
            t = p.get("ticker") or p.get("issuer_name", "")[:16]
            print(f"  {t:<8} put ${p['value_usd']/1e6:.0f}M")


if __name__ == "__main__":
    main()
