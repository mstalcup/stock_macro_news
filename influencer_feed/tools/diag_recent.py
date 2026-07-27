"""Quick diagnostic: recent ISSUE rows, Discord status, CoreEdge FETCH."""
from __future__ import annotations

import argparse
import json

import boto3
from boto3.dynamodb.conditions import Key


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--table", default="influencer-feed-influencer-feed")
    ap.add_argument("--user-id", default="default")
    ap.add_argument(
        "--dates",
        default="2026-05-19,2026-05-18,2026-05-17,2026-05-16,2026-05-15",
    )
    args = ap.parse_args()
    t = boto3.Session(profile_name=args.profile).resource("dynamodb").Table(args.table)
    pk = f"USER#{args.user_id}"

    print("=== ISSUE rows ===")
    for d in args.dates.split(","):
        item = t.get_item(Key={"pk": pk, "sk": f"ISSUE#{d}"}).get("Item")
        if not item:
            print(f"{d}: NO ISSUE")
            continue
        gs = item.get("global_summary") or item.get("overall_advice") or ""
        print(
            f"{d}: discord={item.get('discord_publish_status')} "
            f"slot={item.get('compose_slot')} "
            f"sources={item.get('source_count')} "
            f"has_summary={bool(str(gs).strip())}"
        )

    print("\n=== compose checkpoint ===")
    meta = t.get_item(Key={"pk": pk, "sk": "META#LAST_COMPOSE_WINDOW"}).get("Item")
    print(json.dumps(meta, indent=2, default=str) if meta else "(none)")

    print("\n=== coreedgetrader recent FETCH ===")
    resp = t.query(
        KeyConditionExpression=Key("pk").eq(pk)
        & Key("sk").begins_with("FETCH#coreedgetrader#")
    )
    items = sorted(resp.get("Items", []), key=lambda x: x.get("sk", ""), reverse=True)[:10]
    for i in items:
        print(
            i.get("sk", "")[-40:],
            "|",
            i.get("status"),
            "|",
            (i.get("transcript_fetched_at") or i.get("updated_at") or "")[:22],
            "|",
            (i.get("video_title") or i.get("title") or "")[:55],
        )

    print("\n=== ISSUE_SOURCE coreedge ===")
    for d in args.dates.split(","):
        it = t.get_item(Key={"pk": pk, "sk": f"ISSUE_SOURCE#{d}#coreedgetrader"}).get("Item")
        if not it:
            print(f"{d}: (none)")
        else:
            adv = (it.get("advice") or it.get("summary") or "")[:100]
            print(f"{d}: mode={it.get('source_summary_mode')} advice={adv!r}")


if __name__ == "__main__":
    main()
