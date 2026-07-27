"""Print mark-to-market LLM sentiment performance (runs report in Lambda)."""
from __future__ import annotations

import argparse
import json

import boto3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    args = ap.parse_args()

    lam = boto3.Session(profile_name=args.profile, region_name=args.region).client("lambda")
    resp = lam.invoke(
        FunctionName="llm-sentiment-feed-score-recommendations",
        Payload=json.dumps({"mtm_report": True}).encode(),
    )
    body = json.loads(resp["Payload"].read())
    mtm = body.get("mtm") or {}
    print(json.dumps(mtm, indent=2))

    all_m = mtm.get("all_models") or {}
    print()
    print(f"Today PT: {mtm.get('today_pt')}")
    print(
        f"Picks: {mtm.get('pick_count')} | entries: {mtm.get('with_entry')} | "
        f"pending entry: {mtm.get('pending_entry')} | official T+7: {mtm.get('official_t7_count')}"
    )
    if all_m.get("n"):
        print(
            f"ALL MODELS (MTM): avg {all_m['avg_return_pct']:+.2f}% | "
            f"wins {all_m['wins']}/{all_m['n']} ({all_m['win_rate_pct']}%)"
        )
    for model, agg in (mtm.get("by_model") or {}).items():
        if agg.get("n"):
            print(
                f"  {model}: avg {agg['avg_return_pct']:+.2f}% | "
                f"wins {agg['wins']}/{agg['n']} ({agg['win_rate_pct']}%)"
            )


if __name__ == "__main__":
    main()
