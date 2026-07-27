"""Print deduped headlines from S3 for a date/slot (or latest pointer)."""
from __future__ import annotations

import argparse
import json

import boto3


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--bucket", default="")
    ap.add_argument("--issue-date", default="")
    ap.add_argument("--slot", choices=["pre_open", "pre_close"], default="")
    ap.add_argument("--latest", action="store_true", help="Use v1/latest/slot={slot}.json")
    args = ap.parse_args()

    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    s3 = sess.client("s3")
    cf = sess.client("cloudformation")

    bucket = args.bucket
    if not bucket:
        bucket = cf.describe_stacks(StackName="macro-news-feed")["Stacks"][0]["Outputs"]
        bucket = next(o["OutputValue"] for o in bucket if o["OutputKey"] == "NewsArtifactsBucket")

    if args.latest:
        if not args.slot:
            raise SystemExit("--slot required with --latest")
        key = f"v1/latest/slot={args.slot}.json"
        ptr = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        key = ptr["deduped_s3_key"]
    else:
        if not args.issue_date or not args.slot:
            raise SystemExit("Need --issue-date and --slot, or --latest --slot")
        key = f"v1/date={args.issue_date}/slot={args.slot}/deduped.json"

    doc = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    print(f"{doc.get('issue_date')} {doc.get('slot')} — {doc.get('article_count')} articles\n")
    for i, a in enumerate(doc.get("articles", [])[:20], 1):
        prov = ",".join(a.get("providers", []))
        print(f"{i:2}. [{prov}] {a.get('title', '')[:100]}")
        if a.get("canonical_url"):
            print(f"    {a['canonical_url']}")


if __name__ == "__main__":
    main()
