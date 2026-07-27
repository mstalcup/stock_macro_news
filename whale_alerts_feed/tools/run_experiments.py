"""
Miniature experiment loop — sweep signal hypotheses on cached data.

Goal: find fund/signal combos with positive post-event alpha (excess vs SPY @ 20d).
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whalelib.backtest import compute_forward_alphas, save_json
from whalelib.confluence import link_confluence
from whalelib.types import WhaleSignal

OUT = ROOT / "output"
CACHE = OUT / "cache"
ROSTER = ROOT / "seed" / "fund_roster_curated.json"
SCORED = OUT / "curated_scored_signals.json"
CUTOFF_DEFAULT = "2026-05-26"
TRAIN_END = "2024-12-31"
TEST_START = "2025-01-01"

SIGNAL_TYPES_SCHED = frozenset({"13d_new", "13d_increase", "13g_new", "13g_increase"})


@dataclass
class Experiment:
    id: str
    description: str
    fund_cik: str
    fund_name: str


def _load_json(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _row_to_signal(r: dict) -> WhaleSignal:
    return WhaleSignal(
        signal_id=r.get("signal_id") or "",
        signal_type=r.get("signal_type") or "",
        signal_date=r.get("signal_date") or "",
        filer_cik=str(r.get("filer_cik") or "").lstrip("0"),
        filer_name=r.get("filer_name") or "",
        ticker=(r.get("ticker") or "").upper(),
        issuer_name=r.get("issuer_name") or "",
        accession=r.get("accession") or "",
        alert_class=r.get("alert_class") or "primary",
        meta=r.get("meta") or {},
    )


def _metrics(scored: list[dict], *, as_of: str, after: str | None = None) -> dict:
    rows = [r for r in scored if (r.get("signal_date") or "") <= as_of]
    if after:
        rows = [r for r in rows if (r.get("signal_date") or "") >= after]
    a20 = [r["alpha_20d"] for r in rows if r.get("alpha_20d") is not None]
    a5 = [r["alpha_5d"] for r in rows if r.get("alpha_5d") is not None]
    if not a20:
        return {"n": 0, "n5": len(a5)}
    return {
        "n": len(a20),
        "n5": len(a5),
        "med20": round(statistics.median(a20), 4),
        "med5": round(statistics.median(a5), 4) if a5 else None,
        "hit20": round(sum(1 for x in a20 if x > 0) / len(a20), 4),
        "mean20": round(statistics.mean(a20), 4),
    }


def _dedupe_fund_ticker(signals: list[WhaleSignal]) -> list[WhaleSignal]:
    seen: set[tuple[str, str]] = set()
    out: list[WhaleSignal] = []
    for s in sorted(signals, key=lambda x: x.signal_date or ""):
        k = (s.filer_cik, s.ticker)
        if k in seen:
            continue
        seen.add(k)
        out.append(s)
    return out


def _filter_sched(
    scored_rows: list[dict],
    *,
    cik: str = "",
    types: frozenset[str] | None = None,
    dedupe: bool = False,
) -> list[WhaleSignal]:
    rows = scored_rows
    if cik:
        rows = [r for r in rows if str(r.get("filer_cik", "")).lstrip("0") == cik]
    if types:
        rows = [r for r in rows if r.get("signal_type") in types]
    sigs = [_row_to_signal(r) for r in rows if r.get("signal_type") in SIGNAL_TYPES_SCHED]
    if dedupe:
        sigs = _dedupe_fund_ticker(sigs)
    return sigs


def _volume_for_fund(vol_rows: list[dict], cik: str) -> list[WhaleSignal]:
    return [
        _row_to_signal(r)
        for r in vol_rows
        if str(r.get("filer_cik", "")).lstrip("0") == cik
    ]


def _sched_with_volume_confirm(
    sched: list[WhaleSignal],
    vol_by_ticker_date: dict[tuple[str, str], dict],
    *,
    max_days_before: int = 10,
) -> list[WhaleSignal]:
    """Keep schedule signals that had a volume spike 1..N days before filing."""
    out: list[WhaleSignal] = []
    for s in sched:
        if not s.signal_date or not s.ticker:
            continue
        try:
            fd = date.fromisoformat(s.signal_date)
        except ValueError:
            continue
        ok = False
        for d in range(1, max_days_before + 1):
            vd = (fd - __import__("datetime").timedelta(days=d)).isoformat()
            if (s.ticker, vd) in vol_by_ticker_date:
                ok = True
                break
        if ok:
            out.append(s)
    return out


def _vol_index(vol_rows: list[dict]) -> dict[tuple[str, str], dict]:
    idx: dict[tuple[str, str], dict] = {}
    for r in vol_rows:
        t = (r.get("ticker") or "").upper()
        d = r.get("signal_date") or ""
        if t and d:
            idx[(t, d)] = r
    return idx


def _confluence_signals(all_sigs: list[WhaleSignal], *, cik: str = "") -> list[WhaleSignal]:
    if cik:
        all_sigs = [s for s in all_sigs if s.filer_cik == cik]
    clusters = link_confluence(all_sigs)
    out: list[WhaleSignal] = []
    for c in clusters:
        if len(c.get("layers") or []) < 2:
            continue
        out.append(
            WhaleSignal(
                signal_id=f"confluence#{'+'.join(c['layers'])}#{c['anchor_date']}#{c['ticker']}",
                signal_type="confluence",
                signal_date=c["anchor_date"],
                filer_cik=(c.get("filer_ciks") or [""])[0],
                filer_name="",
                ticker=c["ticker"],
                alert_class="urgent",
                meta={"layers": c["layers"]},
            )
        )
    return out


def _score_signals(signals: list[WhaleSignal], cache: dict[str, list[dict]]) -> list[dict]:
    if not signals:
        return []
    key = json.dumps([(s.signal_id, s.signal_date) for s in signals], sort_keys=True)
    if key in cache:
        return cache[key]
    scored = compute_forward_alphas(signals)
    cache[key] = scored
    return scored


def _works(m: dict, *, min_n: int) -> bool:
    if m.get("n", 0) < min_n:
        return False
    med = m.get("med20")
    hit = m.get("hit20")
    if med is None or hit is None:
        return False
    return med > 0 and hit >= 0.5


def build_experiments(funds: list[dict]) -> list[tuple[Experiment, str]]:
    """Return (experiment, builder) pairs. Builder receives context dict."""
    exps: list[tuple[Experiment, str]] = []

    # Global baselines
    exps.append((Experiment("global_all_13dg", "All 13D/G filings", "", "ALL"), "global_all"))
    exps.append((Experiment("global_13d_only", "All 13D (new+amend)", "", "ALL"), "global_13d"))
    exps.append((Experiment("global_13d_new", "All 13d_new", "", "ALL"), "global_13d_new"))
    exps.append((Experiment("global_13d_dedupe", "13D first per fund+ticker", "", "ALL"), "global_13d_dedupe"))
    exps.append((Experiment("global_volume", "Volume spike (pre-filing)", "", "ALL"), "global_vol"))
    exps.append((Experiment("global_confluence", "2+ layers confluence", "", "ALL"), "global_conf"))

    for f in funds:
        cik = str(f.get("cik") or "").lstrip("0")
        name = f.get("fund_name") or cik
        slug = cik
        exps.extend(
            [
                (Experiment(f"{slug}_13d_new", f"{name}: 13d_new", cik, name), "fund_13d_new"),
                (Experiment(f"{slug}_13d", f"{name}: 13D new+amend", cik, name), "fund_13d"),
                (Experiment(f"{slug}_13g", f"{name}: 13G new+amend", cik, name), "fund_13g"),
                (Experiment(f"{slug}_13d_dedupe", f"{name}: 13D deduped", cik, name), "fund_13d_dedupe"),
                (Experiment(f"{slug}_vol", f"{name}: volume pre-filing", cik, name), "fund_vol"),
                (Experiment(f"{slug}_13d_new_vol", f"{name}: 13d_new + vol confirm", cik, name), "fund_13d_new_vol"),
                (Experiment(f"{slug}_13d_vol", f"{name}: 13D + vol confirm", cik, name), "fund_13d_vol"),
                (Experiment(f"{slug}_conf", f"{name}: confluence", cik, name), "fund_conf"),
            ]
        )

    return exps


def run_builder(
    kind: str,
    ctx: dict,
    *,
    cik: str = "",
) -> list[WhaleSignal]:
    sched = ctx["sched_scored"]
    vol = ctx["vol_raw"]
    eight_k = ctx["eight_k_sigs"]
    news = ctx["news_sigs"]
    vol_idx = ctx["vol_idx"]

    if kind == "global_all":
        return _filter_sched(sched)
    if kind == "global_13d":
        return _filter_sched(sched, types=frozenset({"13d_new", "13d_increase"}))
    if kind == "global_13d_new":
        return _filter_sched(sched, types=frozenset({"13d_new"}))
    if kind == "global_13d_dedupe":
        return _filter_sched(sched, types=frozenset({"13d_new", "13d_increase"}), dedupe=True)
    if kind == "global_vol":
        return [_row_to_signal(r) for r in vol]
    if kind == "global_conf":
        base = _filter_sched(sched) + [_row_to_signal(r) for r in vol] + eight_k + news
        return _confluence_signals(base)

    if kind == "fund_13d_new":
        return _filter_sched(sched, cik=cik, types=frozenset({"13d_new"}))
    if kind == "fund_13d":
        return _filter_sched(sched, cik=cik, types=frozenset({"13d_new", "13d_increase"}))
    if kind == "fund_13g":
        return _filter_sched(sched, cik=cik, types=frozenset({"13g_new", "13g_increase"}))
    if kind == "fund_13d_dedupe":
        return _filter_sched(sched, cik=cik, types=frozenset({"13d_new", "13d_increase"}), dedupe=True)
    if kind == "fund_vol":
        return _volume_for_fund(vol, cik)
    if kind == "fund_13d_new_vol":
        s = _filter_sched(sched, cik=cik, types=frozenset({"13d_new"}))
        return _sched_with_volume_confirm(s, vol_idx)
    if kind == "fund_13d_vol":
        s = _filter_sched(sched, cik=cik, types=frozenset({"13d_new", "13d_increase"}))
        return _sched_with_volume_confirm(s, vol_idx)
    if kind == "fund_conf":
        base = (
            _filter_sched(sched, cik=cik)
            + _volume_for_fund(vol, cik)
            + [s for s in eight_k if s.filer_cik == cik]
            + [s for s in news if s.filer_cik == cik]
        )
        return _confluence_signals(base, cik=cik)

    return []


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--as-of", default=CUTOFF_DEFAULT)
    ap.add_argument("--min-n", type=int, default=4, help="Min scorable signals to call 'works'")
    ap.add_argument("--write-report", action="store_true")
    args = ap.parse_args()

    funds = json.loads(ROSTER.read_text(encoding="utf-8"))
    sched_scored = _load_json(SCORED)
    vol_raw = _load_json(CACHE / "volume_spike_signals.json")
    eight_k_raw = _load_json(CACHE / "8k_signals.json")
    news_raw = _load_json(CACHE / "news_stake_signals.json")

    score_cache: dict[str, list[dict]] = {}
    eight_k_sigs = [_row_to_signal(r) for r in eight_k_raw]
    news_sigs = [_row_to_signal(r) for r in news_raw]
    # Score 8k/news if not in main scored file
    if eight_k_sigs:
        eight_k_scored = compute_forward_alphas(eight_k_sigs)
    else:
        eight_k_scored = []
    if news_sigs:
        news_scored = compute_forward_alphas(news_sigs)
    else:
        news_scored = []

    ctx = {
        "sched_scored": sched_scored,
        "vol_raw": vol_raw,
        "eight_k_sigs": eight_k_sigs,
        "news_sigs": news_sigs,
        "vol_idx": _vol_index(vol_raw),
    }

    experiments = build_experiments(funds)
    results: list[dict] = []

    print(f"Running {len(experiments)} experiments (as-of {args.as_of}, min_n={args.min_n})\n")
    print(f"{'id':<28} {'n':>4} {'med20':>8} {'hit':>6} {'test_n':>6} {'test_med':>9} {'OK':>4}")
    print("-" * 72)

    for exp, kind in experiments:
        cik = exp.fund_cik
        sigs = run_builder(kind, ctx, cik=cik)
        if kind.startswith("global") and exp.id == "global_confluence":
            pass
        scored = _score_signals(sigs, score_cache)
        full = _metrics(scored, as_of=args.as_of)
        test = _metrics(scored, as_of=args.as_of, after=TEST_START)
        train = _metrics(scored, as_of=TRAIN_END)
        ok = _works(full, min_n=args.min_n)
        # OOS: test should not be terrible if in-sample works
        oos_ok = test.get("n", 0) >= 2 and (test.get("med20") or -999) > -2

        row = {
            "id": exp.id,
            "description": exp.description,
            "fund_cik": exp.fund_cik,
            "fund_name": exp.fund_name,
            "kind": kind,
            "full": full,
            "train": train,
            "test": test,
            "works_insample": ok,
            "works_oos_hint": oos_ok and ok,
        }
        results.append(row)

        med = full.get("med20")
        hit = full.get("hit20")
        med_s = f"{med:+.2f}%" if med is not None else "  -"
        hit_s = f"{(hit or 0)*100:.0f}%" if full.get("n") else "  -"
        tmed = test.get("med20")
        tmed_s = f"{tmed:+.2f}%" if tmed is not None else "  -"
        flag = "YES" if ok else ""
        print(
            f"{exp.id:<28} {full.get('n',0):>4} {med_s:>8} {hit_s:>6} "
            f"{test.get('n',0):>6} {tmed_s:>9} {flag:>4}"
        )

    winners = [
        r
        for r in results
        if r["works_insample"]
        and r["full"].get("n", 0) >= args.min_n
    ]
    winners.sort(key=lambda x: (x["full"].get("med20") or -999, x["full"].get("hit20") or 0), reverse=True)

    print(f"\n=== WINNERS (n>={args.min_n}, med20>0, hit>=50%) ===")
    if not winners:
        print("No experiment met criteria. Relaxing to n>=3, hit>=45%...")
        winners = [
            r for r in results
            if r["full"].get("n", 0) >= 3
            and (r["full"].get("med20") or -999) > 0
            and (r["full"].get("hit20") or 0) >= 0.45
        ]
        winners.sort(key=lambda x: (x["full"].get("med20") or -999), reverse=True)

    for r in winners[:15]:
        f = r["full"]
        t = r["test"]
        print(
            f"  {r['description']}: n={f.get('n')} med20={f.get('med20'):+.2f}% "
            f"hit={f.get('hit20',0)*100:.0f}% | test n={t.get('n')} med20={t.get('med20')}"
        )

    save_json(OUT / "experiment_results.json", results)

    if args.write_report:
        lines = [
            "# Signal experiment sweep",
            "",
            f"As-of: {args.as_of} | Min n: {args.min_n}",
            "",
            "## Winners",
            "",
        ]
        if winners:
            for r in winners[:20]:
                f, t = r["full"], r["test"]
                lines.append(
                    f"- **{r['description']}** — n={f.get('n')}, med α20d={f.get('med20')}%, "
                    f"hit={round((f.get('hit20') or 0)*100,1)}% "
                    f"(test n={t.get('n')}, test med={t.get('med20')})"
                )
        else:
            lines.append("_No winners at threshold._")
        lines.extend(["", "## Top 10 by med20 (any n>=3)", ""])
        ranked = sorted(
            [r for r in results if r["full"].get("n", 0) >= 3],
            key=lambda x: x["full"].get("med20") or -999,
            reverse=True,
        )[:10]
        for r in ranked:
            f = r["full"]
            lines.append(
                f"- {r['description']}: n={f.get('n')}, med20={f.get('med20')}%, hit={round((f.get('hit20') or 0)*100,1)}%"
            )
        lines.extend(
            [
                "",
                "## Recommendation",
                "",
                "Deploy the top winner(s) with `works_oos_hint` and enough test-period n.",
                "Prefer **13d_new + volume confirm** or **per-fund volume** over blind 13G.",
            ]
        )
        path = OUT / "experiment_report.md"
        path.write_text("\n".join(lines), encoding="utf-8")
        print(f"\nWrote {path}")


if __name__ == "__main__":
    main()
