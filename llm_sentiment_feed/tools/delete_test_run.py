"""Delete test/ prefixed DynamoDB picks and S3 artifacts for an issue date."""
from __future__ import annotations

import argparse

import boto3
from boto3.dynamodb.conditions import Attr, Key


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--stack", default="llm-sentiment-feed")
    ap.add_argument("--issue-date", required=True)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    cf = sess.client("cloudformation")
    outs = {o["OutputKey"]: o["OutputValue"] for o in cf.describe_stacks(StackName=args.stack)["Stacks"][0]["Outputs"]}
    table = sess.resource("dynamodb").Table(outs["SentimentTableName"])
    bucket = outs["SentimentArtifactsBucket"]
    s3 = sess.client("s3")

    pk = f"test/ISSUE#{args.issue_date}"
    deleted = 0
    resp = table.query(KeyConditionExpression=Key("pk").eq(pk))
    items = list(resp.get("Items", []))
    while resp.get("LastEvaluatedKey"):
        resp = table.query(
            KeyConditionExpression=Key("pk").eq(pk),
            ExclusiveStartKey=resp["LastEvaluatedKey"],
        )
        items.extend(resp.get("Items", []))

    scan = table.scan(FilterExpression=Attr("sk").begins_with(f"test/PICK#{args.issue_date}#"))
    items.extend(scan.get("Items", []))
    while scan.get("LastEvaluatedKey"):
        scan = table.scan(
            FilterExpression=Attr("sk").begins_with(f"test/PICK#{args.issue_date}#"),
            ExclusiveStartKey=scan["LastEvaluatedKey"],
        )
        items.extend(scan.get("Items", []))

    seen = set()
    for item in items:
        key = (item["pk"], item["sk"])
        if key in seen:
            continue
        seen.add(key)
        if args.dry_run:
            print(f"would delete DDB {key}")
        else:
            table.delete_item(Key={"pk": item["pk"], "sk": item["sk"]})
        deleted += 1

    prefix = f"test/v1/issue_date={args.issue_date}/"
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            k = obj["Key"]
            if args.dry_run:
                print(f"would delete S3 {k}")
            else:
                s3.delete_object(Bucket=bucket, Key=k)
            deleted += 1

    print(f"{'would remove' if args.dry_run else 'removed'} {deleted} objects/rows")


if __name__ == "__main__":
    main()
