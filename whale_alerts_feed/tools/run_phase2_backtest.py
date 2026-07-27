"""Score Phase 2 layers + confluence; answer 'would each signal type have worked?'"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from whalelib.backtest import compute_forward_alphas, layer_summary, save_json, write_phase2_report
from whalelib.confluence import confluence_to_signals, link_confluence
from whalelib.types import WhaleSignal

CACHE = ROOT / "output" / "cache"
OUT = ROOT / "output"
CURATED_SCORED = OUT / "curated_scored_signals.json"


def _load_whale_signals(path: Path, *, signal_type: str | None = None) -> list[WhaleSignal]:
    if not path.is_file():
        return []
    rows = json.loads(path.read_text(encoding="utf-8"))
    out: list[WhaleSignal] = []
    for r in rows:
        if signal_type and r.get("signal_type") != signal_type:
            continue
        out.append(
            WhaleSignal(
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
        )
    return out


def _schedule_from_curated() -> list[WhaleSignal]:
    if not CURATED_SCORED.is_file():
        return []
    rows = json.loads(CURATED_SCORED.read_text(encoding="utf-8"))
    out: list[WhaleSignal] = []
    for r in rows:
        st = r.get("signal_type") or ""
        if not st.startswith("13"):
            continue
        out.append(
            WhaleSignal(
                signal_id=r.get("signal_id") or "",
                signal_type=st,
                signal_date=r.get("signal_date") or "",
                filer_cik=str(r.get("filer_cik") or "").lstrip("0"),
                filer_name=r.get("filer_name") or "",
                ticker=(r.get("ticker") or "").upper(),
                alert_class="primary",
            )
        )
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-report", action="store_true")
    ap.add_argument("--skip-schedule", action="store_true", help="Omit layer-1 13D/G from comparison")
    args = ap.parse_args()

    layers: dict[str, list[WhaleSignal]] = {}
    if not args.skip_schedule:
        layers["schedule_13dg"] = _schedule_from_curated()
    layers["8k_stake"] = _load_whale_signals(CACHE / "8k_signals.json")
    layers["volume_spike"] = _load_whale_signals(CACHE / "volume_spike_signals.json")
    layers["news_stake"] = _load_whale_signals(CACHE / "news_stake_signals.json")

    if not any(layers.values()):
        print("No phase-2 signals found. Run: py tools/backfill_phase2.py")
        raise SystemExit(1)
    empty = [k for k, v in layers.items() if not v]
    if empty:
        print(f"Note: empty layers {empty} — run backfill or set API keys")

    all_signals: list[WhaleSignal] = []
    layer_stats: list[dict] = []
    scored_by_layer: dict[str, list[dict]] = {}

    print("Phase 2 backtest — per-layer forward returns vs SPY\n")
    print(f"{'layer':<18} {'n':>5} {'med a20d':>10} {'hit%':>8}")
    print("-" * 45)

    for name, sigs in layers.items():
        if not sigs:
            print(f"{name:<18}     0        —        —")
            continue
        scored = compute_forward_alphas(sigs)
        scored_by_layer[name] = scored
        all_signals.extend(sigs)
        stat = layer_summary(scored, layer=name)
        layer_stats.append(stat)
        med = stat.get("median_alpha_20d")
        hit = stat.get("hit_rate_20d")
        n = stat.get("with_alpha_20d") or 0
        med_s = f"{med:+.2f}%" if med is not None else "—"
        hit_s = f"{hit*100:.1f}%" if hit is not None else "—"
        print(f"{name:<18} {n:>5} {med_s:>10} {hit_s:>8}")

    clusters = link_confluence(all_signals)
    conf_sigs = confluence_to_signals(clusters)
    conf_scored = compute_forward_alphas(conf_sigs)
    cf = layer_summary(conf_scored, layer="confluence")
    layer_stats.append(cf)
    med = cf.get("median_alpha_20d")
    hit = cf.get("hit_rate_20d")
    n = cf.get("with_alpha_20d") or 0
    med_s = f"{med:+.2f}%" if med is not None else "—"
    hit_s = f"{hit*100:.1f}%" if hit is not None else "—"
    print(f"{'confluence':<18} {n:>5} {med_s:>10} {hit_s:>8}  ({len(clusters)} clusters)")

    OUT.mkdir(parents=True, exist_ok=True)
    save_json(OUT / "phase2_layer_stats.json", layer_stats)
    save_json(OUT / "phase2_confluence.json", clusters)
    save_json(OUT / "phase2_scored.json", {k: v for k, v in scored_by_layer.items()})

    if args.write_report:
        write_phase2_report(
            OUT / "phase2_backtest_report.md",
            layer_stats=layer_stats,
            confluence_scored=conf_scored,
            clusters=clusters,
        )
        print(f"\nWrote {OUT / 'phase2_backtest_report.md'}")


if __name__ == "__main__":
    main()
