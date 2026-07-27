"""Merge curated roster + backtest rankings + manual overrides → seed/fund_roster.json"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--curated-only", action="store_true", help="Deploy roster from curated list only")
    args = ap.parse_args()

    roster: list[dict] = []
    seen: set[str] = set()

    for path in (ROOT / "seed" / "manual_overrides.json", ROOT / "seed" / "fund_roster_curated.json"):
        if not path.is_file():
            continue
        for row in json.loads(path.read_text(encoding="utf-8")):
            cik = str(row.get("cik") or row.get("fund_id") or "").lstrip("0")
            if not cik or cik in seen:
                continue
            seen.add(cik)
            roster.append(
                {
                    "fund_id": cik,
                    "fund_name": row.get("fund_name") or f"CIK {cik}",
                    "cik": cik,
                    "tier": row.get("tier") or "B",
                    "source": row.get("source") or path.stem,
                    **({"override_reason": row["override_reason"]} if row.get("override_reason") else {}),
                }
            )

    if not args.curated_only:
        rank_path = ROOT / "output" / "fund_rankings.json"
        if rank_path.is_file():
            for r in json.loads(rank_path.read_text(encoding="utf-8")):
                if r.get("status") != "ranked":
                    continue
                cik = str(r.get("filer_cik") or "").lstrip("0")
                if not cik or cik in seen:
                    continue
                seen.add(cik)
                roster.append(
                    {
                        "fund_id": cik,
                        "fund_name": r.get("filer_name") or f"CIK {cik}",
                        "cik": cik,
                        "tier": "B",
                        "source": "backtest_rank",
                        "composite_score": r.get("composite_score"),
                    }
                )

    out = ROOT / "seed" / "fund_roster.json"
    out.write_text(json.dumps(roster, indent=2), encoding="utf-8")
    print(f"Wrote {len(roster)} funds to {out}")


if __name__ == "__main__":
    main()
