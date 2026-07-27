"""Sweep alert filter strategies on cached curated_scored_signals.json."""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "output"
SCORED_PATH = OUT / "curated_scored_signals.json"
ROSTER_PATH = ROOT / "seed" / "fund_roster_curated.json"

VOL_FILERS = frozenset({"1273087", "1603466"})  # Millennium, Point72
BERKSHIRE = frozenset({"1067983"})
ACTIVIST_CIKS = frozenset(
    {
        "2045724",
        "1336528",
        "1649339",
        "1350694",
        "1040273",
        "1138995",
        "1364742",
        "1517137",
        "1656456",
        "1709323",
    }
)
GREENLIGHT_STARBOARD = frozenset({"1364742", "1517137"})
GREENLIGHT = "1364742"
STARBOARD = "1517137"


def load() -> tuple[list[dict], dict[str, str], dict[str, str]]:
    scored = json.loads(SCORED_PATH.read_text(encoding="utf-8"))
    funds = json.loads(ROSTER_PATH.read_text(encoding="utf-8"))
    tier = {str(f["cik"]).lstrip("0"): f.get("tier", "") for f in funds}
    names = {str(f["cik"]).lstrip("0"): f.get("fund_name", "") for f in funds}
    return scored, tier, names


def _hybrid_starboard_greenlight(row: dict) -> bool:
    cik = row.get("filer_cik") or ""
    st = row.get("signal_type") or ""
    if cik == STARBOARD and st == "13d_new":
        return True
    return cik == GREENLIGHT and st.startswith("13g")


def filter_signals(
    scored: list[dict],
    *,
    types: frozenset[str] | None = None,
    ciks: frozenset[str] | None = None,
    exclude_ciks: frozenset[str] | None = None,
    tiers: frozenset[str] | None = None,
    tier_map: dict[str, str] | None = None,
    dedupe_fund_ticker: bool = False,
    as_of: str | None = None,
    custom: str | None = None,
) -> list[dict]:
    rows = scored
    if custom == "hybrid_starboard_greenlight":
        rows = [r for r in rows if _hybrid_starboard_greenlight(r)]
    if types:
        rows = [r for r in rows if r.get("signal_type") in types]
    if ciks:
        rows = [r for r in rows if r.get("filer_cik") in ciks]
    if exclude_ciks:
        rows = [r for r in rows if r.get("filer_cik") not in exclude_ciks]
    if tiers and tier_map:
        rows = [r for r in rows if tier_map.get(r.get("filer_cik", "")) in tiers]
    if as_of:
        rows = [r for r in rows if (r.get("signal_date") or "") <= as_of]
    if dedupe_fund_ticker:
        seen: set[tuple[str, str]] = set()
        deduped: list[dict] = []
        for r in sorted(rows, key=lambda x: x.get("signal_date") or ""):
            key = (r.get("filer_cik") or "", r.get("ticker") or "")
            if key in seen:
                continue
            seen.add(key)
            deduped.append(r)
        rows = deduped
    return rows


def metrics(rows: list[dict], horizon: str = "20d") -> dict:
    key = f"alpha_{horizon}"
    vals = [r[key] for r in rows if r.get(key) is not None]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "median": round(statistics.median(vals), 4),
        "hit_pct": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1),
        "mean": round(statistics.mean(vals), 4),
    }


def walk_forward(
    rows: list[dict],
    *,
    train_end: str,
    test_start: str,
    horizon: str = "20d",
) -> dict:
    """Rank funds on train window; alert only on train winners in test window."""
    key = f"alpha_{horizon}"
    train = [r for r in rows if (r.get("signal_date") or "") <= train_end and r.get(key) is not None]
    test = [r for r in rows if (r.get("signal_date") or "") >= test_start and r.get(key) is not None]
    by_cik: dict[str, list[float]] = {}
    for r in train:
        by_cik.setdefault(r.get("filer_cik") or "", []).append(r[key])
    winners = {cik for cik, vals in by_cik.items() if len(vals) >= 2 and statistics.median(vals) > 0}
    test_filtered = [r for r in test if r.get("filer_cik") in winners]
    return {
        "train_n": len(train),
        "test_n": len(test_filtered),
        "winners": len(winners),
        "test": metrics(test_filtered, horizon),
    }


STRATEGIES: list[tuple[str, dict]] = [
    ("baseline_all", {}),
    ("excl_volume_filers", {"exclude_ciks": VOL_FILERS}),
    ("excl_volume_and_berkshire", {"exclude_ciks": VOL_FILERS | BERKSHIRE}),
    ("13d_only", {"types": frozenset({"13d_new", "13d_increase"})}),
    ("13d_new_only", {"types": frozenset({"13d_new"})}),
    ("13d_dedupe", {"types": frozenset({"13d_new", "13d_increase"}), "dedupe_fund_ticker": True}),
    (
        "13d_dedupe_excl_noise",
        {
            "types": frozenset({"13d_new", "13d_increase"}),
            "exclude_ciks": VOL_FILERS | BERKSHIRE,
            "dedupe_fund_ticker": True,
        },
    ),
    (
        "13d_dedupe_activist",
        {
            "types": frozenset({"13d_new", "13d_increase"}),
            "ciks": ACTIVIST_CIKS,
            "dedupe_fund_ticker": True,
        },
    ),
    (
        "13d_dedupe_greenlight_starboard",
        {
            "types": frozenset({"13d_new", "13d_increase"}),
            "ciks": GREENLIGHT_STARBOARD,
            "dedupe_fund_ticker": True,
        },
    ),
    (
        "13d_new_activist_dedupe",
        {
            "types": frozenset({"13d_new"}),
            "ciks": ACTIVIST_CIKS,
            "dedupe_fund_ticker": True,
        },
    ),
    (
        "starboard_13d_new",
        {"types": frozenset({"13d_new"}), "ciks": frozenset({STARBOARD})},
    ),
    (
        "greenlight_13g",
        {"types": frozenset({"13g_new", "13g_increase"}), "ciks": frozenset({GREENLIGHT})},
    ),
    (
        "hybrid_starboard13d_greenlight13g",
        {"custom": "hybrid_starboard_greenlight"},
    ),
]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=date.today().isoformat())
    ap.add_argument("--write-report", action="store_true")
    args = ap.parse_args()

    scored, tier, names = load()
    results: list[dict] = []

    print(f"Strategy sweep (as-of {args.as_of})\n")
    print(f"{'strategy':<35} {'n':>5} {'med5d':>8} {'hit5':>6} {'med20':>8} {'hit20':>6} {'med60':>8}")
    print("-" * 82)

    for label, kw in STRATEGIES:
        kw = dict(kw)
        rows = filter_signals(scored, tier_map=tier, as_of=args.as_of, **kw)
        if label == "hybrid_starboard13d_greenlight13g":
            rows = filter_signals(rows, dedupe_fund_ticker=True)
        m5 = metrics(rows, "5d")
        m20 = metrics(rows, "20d")
        m60 = metrics(rows, "60d")
        row = {
            "strategy": label,
            "filters": {k: sorted(v) if isinstance(v, frozenset) else v for k, v in kw.items()},
            "metrics_5d": m5,
            "metrics_20d": m20,
            "metrics_60d": m60,
        }
        results.append(row)
        med5 = f"{m5['median']:+.2f}%" if m5.get("n") else "—"
        hit5 = f"{m5.get('hit_pct', 0):.0f}%" if m5.get("n") else "—"
        med20 = f"{m20['median']:+.2f}%" if m20.get("n") else "—"
        hit20 = f"{m20.get('hit_pct', 0):.0f}%" if m20.get("n") else "—"
        med60 = f"{m60['median']:+.2f}%" if m60.get("n") else "—"
        n = m20.get("n") or 0
        print(f"{label:<35} {n:>5} {med5:>8} {hit5:>6} {med20:>8} {hit20:>6} {med60:>8}")

    # Walk-forward on best candidate
    best_rows = filter_signals(
        scored,
        types=frozenset({"13d_new", "13d_increase"}),
        exclude_ciks=VOL_FILERS | BERKSHIRE,
        dedupe_fund_ticker=True,
        as_of=args.as_of,
    )
    wf = walk_forward(best_rows, train_end="2025-01-01", test_start="2025-01-02")
    print("\nWalk-forward (13d_dedupe_excl_noise, train<=2025, test>=2025):")
    print(f"  train winners: {wf['winners']}, test n={wf['test_n']}, test med20={wf['test'].get('median')}, hit={wf['test'].get('hit_pct')}%")

    hybrid = filter_signals(scored, custom="hybrid_starboard_greenlight", as_of=args.as_of)
    hybrid = filter_signals(hybrid, dedupe_fund_ticker=True)
    for train_end, test_start in [("2024-12-31", "2025-01-01"), ("2025-06-30", "2025-07-01")]:
        train = [r for r in hybrid if (r.get("signal_date") or "") <= train_end]
        test = [r for r in hybrid if (r.get("signal_date") or "") >= test_start]
        mt = metrics(train, "20d")
        xt = metrics(test, "20d")
        print(
            f"Walk-forward hybrid (train<={train_end}): train n={mt.get('n')} med20={mt.get('median')}% "
            f"| test n={xt.get('n')} med20={xt.get('median')}% hit={xt.get('hit_pct')}%"
        )

    ranked = sorted(
        [r for r in results if r["metrics_20d"].get("n", 0) >= 8],
        key=lambda x: (x["metrics_20d"].get("median") or -999, x["metrics_20d"].get("hit_pct") or 0),
        reverse=True,
    )
    print("\nTop strategies (n>=8, by med20):")
    for r in ranked[:5]:
        m = r["metrics_20d"]
        print(f"  {r['strategy']}: n={m['n']} med20={m['median']:+.2f}% hit={m['hit_pct']}%")

    if args.write_report:
        lines = [
            "# Strategy sweep — curated 13D/G alerts",
            "",
            f"As-of: {args.as_of}",
            "",
            "| Strategy | n | med α5d | hit 5d | med α20d | hit 20d | med α60d |",
            "|----------|---|---------|--------|----------|---------|----------|",
        ]
        for r in results:
            m5, m20, m60 = r["metrics_5d"], r["metrics_20d"], r["metrics_60d"]
            lines.append(
                f"| {r['strategy']} | {m20.get('n', 0)} | "
                f"{m5.get('median', '—')} | {m5.get('hit_pct', '—')} | "
                f"{m20.get('median', '—')} | {m20.get('hit_pct', '—')} | "
                f"{m60.get('median', '—')} |"
            )
        best = ranked[0] if ranked else None
        lines.extend(["", "## Recommendation", ""])
        if best and (best["metrics_20d"].get("median") or 0) > 0:
            lines.extend(
                [
                    f"**Deploy:** `{best['strategy']}` — med α20d {best['metrics_20d'].get('median')}% "
                    f"over {best['metrics_20d'].get('n')} signals (in-sample).",
                    "",
                    "Fund-specific hybrid (Starboard 13d_new + Greenlight 13g) is implemented in `whalelib/alert_policy.py`.",
                    "Exclude Millennium/Point72 13G; default activist funds get 13D-only alerts.",
                ]
            )
        else:
            lines.extend(
                [
                    "No robust profitable filter found on 13D/G alone. Consider Phase 2: 8-K stakes, volume, news.",
                ]
            )
        path = OUT / "strategy_sweep_report.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        save = OUT / "strategy_sweep_results.json"
        save.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
