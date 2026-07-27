"""Print composed macro digest from S3."""
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
    ap.add_argument("--latest", action="store_true")
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
        ptr = json.loads(s3.get_object(Bucket=bucket, Key=f"v1/latest/slot={args.slot}.json")["Body"].read())
        key = ptr.get("digest_s3_key")
        if not key:
            raise SystemExit("No digest_s3_key on latest pointer — run compose first")
    else:
        if not args.issue_date or not args.slot:
            raise SystemExit("Need --issue-date and --slot, or --latest --slot")
        key = f"v1/date={args.issue_date}/slot={args.slot}/digest.json"

    doc = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
    print(doc.get("digest_markdown") or json.dumps(doc.get("digest"), indent=2))


if __name__ == "__main__":
    main()
