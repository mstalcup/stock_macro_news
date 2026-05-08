#!/usr/bin/env python3
"""
Home-runner: fetch YouTube transcripts from a residential/home IP and upload JSON to S3.

S3 key layout (stable for downstream Step Functions / compose jobs):
  s3://{bucket}/v1/user/{user_id}/issue/{issue_date}/source/{source_id}/video/{video_id}.json
  s3://{bucket}/v1/user/{user_id}/issue/{issue_date}/source/{source_id}/manifest.json

Each video JSON contains metadata + transcript text. manifest.json lists artifacts for that source/day.

Usage (from repo machine with AWS creds + pip deps installed):
  python tools/fetch_transcripts_home.py \\
    --table influencer-feed-influencer-feed \\
    --bucket YOUR_TRANSCRIPT_BUCKET \\
    --issue-date 2026-05-03 \\
    --user-id default

  # Last 14 America/New_York calendar days (oldest first), optional ingest if Dynamo has no FETCH rows:
  python tools/fetch_transcripts_home.py --table ... --bucket ... --days-back 14 \\
    --ensure-find-content --stack-name influencer-feed --user-id default
"""

from __future__ import annotations

import argparse
import json
import random
import re
import time
from datetime import datetime, timedelta, timezone
from http.cookiejar import MozillaCookieJar
from typing import Any
from zoneinfo import ZoneInfo

import boto3
import requests
from botocore.exceptions import ClientError
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api.proxies import GenericProxyConfig, WebshareProxyConfig

_TRANSCRIPT_API: YouTubeTranscriptApi | None = None
_COOKIES_FILE: str | None = None
_PROXY_URLS: list[str] = []
_PROXY_CURSOR = 0


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def et_issue_dates(days_back: int) -> list[str]:
    """Last ``days_back`` calendar dates in America/New_York, oldest first."""
    if days_back < 1:
        raise ValueError("days_back must be >= 1")
    tz = ZoneInfo("America/New_York")
    end = datetime.now(tz).date()
    return [(end - timedelta(days=d)).isoformat() for d in range(days_back - 1, -1, -1)]


def transcript_s3_prefix(*, schema: str, user_id: str, issue_date: str, source_id: str) -> str:
    return f"{schema}/user/{user_id}/issue/{issue_date}/source/{source_id}/"


def transcript_video_key(prefix: str, video_id: str) -> str:
    return f"{prefix}video/{video_id}.json"


def manifest_key(prefix: str) -> str:
    return f"{prefix}manifest.json"


def _chunks_to_text(chunks: list[Any]) -> str:
    parts: list[str] = []
    for chunk in chunks:
        text = getattr(chunk, "text", None)
        if text is None and isinstance(chunk, dict):
            text = chunk.get("text", "")
        text = (text or "").replace("\n", " ").strip()
        if text:
            parts.append(text)
    return " ".join(parts).strip()


def _create_http_client() -> requests.Session | None:
    if not _COOKIES_FILE:
        return None
    jar = MozillaCookieJar(_COOKIES_FILE)
    jar.load(ignore_discard=True, ignore_expires=True)
    http_client = requests.Session()
    http_client.cookies = jar
    return http_client


def _parse_proxy_list_line(raw_line: str) -> str:
    line = raw_line.strip()
    if not line or line.startswith("#"):
        return ""
    if line.startswith("http://") or line.startswith("https://"):
        return line
    parts = line.split(":")
    if len(parts) == 4:
        host, port, user, password = parts
        return f"http://{user}:{password}@{host}:{port}"
    raise ValueError(
        "Invalid proxy line format. Use either URL form or host:port:username:password"
    )


def _load_proxy_urls(proxy_list_file: str) -> list[str]:
    urls: list[str] = []
    with open(proxy_list_file, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            parsed = _parse_proxy_list_line(line)
            if not parsed:
                continue
            urls.append(parsed)
    if not urls:
        raise SystemExit(f"No usable proxy entries found in {proxy_list_file}")
    return urls


def _make_api_for_proxy_url(proxy_url: str) -> YouTubeTranscriptApi:
    proxy_config = GenericProxyConfig(http_url=proxy_url, https_url=proxy_url)
    return YouTubeTranscriptApi(
        proxy_config=proxy_config,
        http_client=_create_http_client(),
    )


def _next_proxy_url() -> str:
    global _PROXY_CURSOR
    if not _PROXY_URLS:
        raise RuntimeError("Proxy URL pool is empty")
    url = _PROXY_URLS[_PROXY_CURSOR % len(_PROXY_URLS)]
    _PROXY_CURSOR += 1
    return url


def configure_transcript_client(args: argparse.Namespace) -> None:
    global _TRANSCRIPT_API, _COOKIES_FILE, _PROXY_URLS, _PROXY_CURSOR

    proxy_config = None
    _COOKIES_FILE = args.cookies_file
    _PROXY_URLS = []
    _PROXY_CURSOR = 0

    if args.proxy_list_file:
        _PROXY_URLS = _load_proxy_urls(args.proxy_list_file)
        print(f"Transcript client: proxy list mode enabled ({len(_PROXY_URLS)} entries).")
        _TRANSCRIPT_API = _make_api_for_proxy_url(_next_proxy_url())
        if _COOKIES_FILE:
            print(f"Transcript client: loaded cookies from {_COOKIES_FILE}.")
        return

    webshare_enabled = bool(args.webshare_proxy_username or args.webshare_proxy_password)
    generic_proxy_enabled = bool(args.proxy_http_url or args.proxy_https_url)

    if webshare_enabled:
        if not (args.webshare_proxy_username and args.webshare_proxy_password):
            raise SystemExit(
                "Both --webshare-proxy-username and --webshare-proxy-password are required when using Webshare."
            )
        locations = None
        if args.webshare_filter_ip_locations:
            locations = [x.strip() for x in args.webshare_filter_ip_locations.split(",") if x.strip()]
        proxy_config = WebshareProxyConfig(
            proxy_username=args.webshare_proxy_username,
            proxy_password=args.webshare_proxy_password,
            filter_ip_locations=locations,
            retries_when_blocked=args.webshare_retries_when_blocked,
        )
        print("Transcript client: Webshare rotating proxy enabled.")
    elif generic_proxy_enabled:
        proxy_config = GenericProxyConfig(
            http_url=args.proxy_http_url,
            https_url=args.proxy_https_url,
        )
        print("Transcript client: generic proxy URLs enabled.")

    http_client = _create_http_client()
    if _COOKIES_FILE:
        print(f"Transcript client: loaded cookies from {_COOKIES_FILE}.")

    _TRANSCRIPT_API = YouTubeTranscriptApi(
        proxy_config=proxy_config,
        http_client=http_client,
    )


def fetch_transcript_text(video_id: str) -> tuple[str, str, str]:
    """Return (text, label, error)."""
    last_err = ""
    try:
        api = _TRANSCRIPT_API or YouTubeTranscriptApi()
        transcript_list = api.list(video_id)
    except Exception as exc:
        return "", "", f"list failed: {type(exc).__name__}: {exc}"

    try:
        t = transcript_list.find_transcript(["en", "en-US", "en-GB"])
        text = _chunks_to_text(t.fetch())
        if text:
            return text, "caption:en", ""
    except Exception as exc:
        last_err = f"en: {type(exc).__name__}: {exc}"

    try:
        t = transcript_list.find_generated_transcript(["en", "en-US", "en-GB"])
        text = _chunks_to_text(t.fetch())
        if text:
            return text, "caption:en-generated", ""
    except Exception as exc:
        last_err = f"en-generated: {type(exc).__name__}: {exc}"

    for lang in ("zh-Hans", "zh-Hant", "zh", "zh-CN", "zh-TW"):
        try:
            t = transcript_list.find_transcript([lang])
            if getattr(t, "is_translatable", False):
                text = _chunks_to_text(t.translate("en").fetch())
                if text:
                    return text, "caption:zh->en", ""
            text = _chunks_to_text(t.fetch())
            if text:
                return text, "caption:zh", ""
        except Exception as exc:
            last_err = f"{lang}: {type(exc).__name__}: {exc}"

    try:
        for t in transcript_list:
            try:
                if getattr(t, "is_translatable", False):
                    text = _chunks_to_text(t.translate("en").fetch())
                    if text:
                        return text, "caption:any->en", ""
                text = _chunks_to_text(t.fetch())
                if text:
                    return text, "caption:any", ""
            except Exception as exc:
                last_err = f"iterate: {type(exc).__name__}: {exc}"
    except Exception:
        pass

    return "", "", last_err or "no transcript found"


def fetch_transcript_text_with_retry(
    video_id: str,
    *,
    max_attempts: int,
    initial_backoff_seconds: float,
    max_backoff_seconds: float,
    jitter_ratio: float,
) -> tuple[str, str, str]:
    """
    Retry transcript fetch with exponential backoff and jitter.

    This favors reliability and low request pressure over speed.
    """
    last_err = ""
    for attempt in range(1, max_attempts + 1):
        if _PROXY_URLS:
            # Rotate proxy on every attempt to reduce chance of sticky blocked IPs.
            global _TRANSCRIPT_API
            _TRANSCRIPT_API = _make_api_for_proxy_url(_next_proxy_url())
        text, label, err = fetch_transcript_text(video_id)
        if text:
            return text, label, ""
        last_err = err or "empty transcript response"
        if attempt >= max_attempts:
            break

        base_sleep = min(max_backoff_seconds, initial_backoff_seconds * (2 ** (attempt - 1)))
        jitter = base_sleep * jitter_ratio * random.random()
        sleep_for = base_sleep + jitter
        print(
            f"    transcript retry {attempt}/{max_attempts - 1} for {video_id}; "
            f"sleep {sleep_for:.1f}s; last_err={last_err}"
        )
        time.sleep(sleep_for)

    return "", "", last_err


def _parse_video_ids(item: dict[str, Any]) -> list[str]:
    raw = item.get("video_ids") or []
    out: list[str] = []
    for v in raw:
        if isinstance(v, str):
            out.append(v)
        elif isinstance(v, dict) and "S" in v:
            out.append(v["S"])
    return out


def _parse_source_id_from_sk(sk: str, item: dict[str, Any]) -> str:
    if item.get("source_id"):
        sid = item["source_id"]
        return sid if isinstance(sid, str) else str(sid)
    m = re.match(r"^FETCH#[^#]+#(.+)$", sk)
    return m.group(1) if m else sk


def s3_object_exists(s3, bucket: str, key: str) -> bool:
    try:
        s3.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("404", "NotFound", "NoSuchKey"):
            return False
        raise


def count_fetch_prefix(table, pk: str, issue_date: str) -> int:
    skp = f"FETCH#{issue_date}#"
    total = 0
    eks: dict[str, Any] | None = None
    while True:
        kw: dict[str, Any] = {
            "KeyConditionExpression": "pk = :pk AND begins_with(sk, :skp)",
            "ExpressionAttributeValues": {":pk": pk, ":skp": skp},
            "Select": "COUNT",
        }
        if eks:
            kw["ExclusiveStartKey"] = eks
        resp = table.query(**kw)
        total += int(resp["Count"])
        eks = resp.get("LastEvaluatedKey")
        if not eks:
            break
    return total


def resolve_find_content_sm_arn(cfn, stack_name: str) -> str:
    resp = cfn.describe_stacks(StackName=stack_name)
    stacks = resp.get("Stacks") or []
    if not stacks:
        raise SystemExit(f"Stack not found: {stack_name}")
    for out in stacks[0].get("Outputs", []) or []:
        if out.get("OutputKey") == "FindContentStateMachineArn":
            v = out.get("OutputValue")
            if v:
                return str(v)
    raise SystemExit(f"No FindContentStateMachineArn output on stack {stack_name}")


def run_find_content_and_wait(
    states,
    *,
    sm_arn: str,
    issue_date: str,
    user_id: str,
    poll_seconds: float = 3.0,
    max_polls: int = 200,
) -> None:
    name = f"bf-{issue_date.replace('-', '')}-{int(time.time())}"
    if len(name) > 80:
        name = name[:80]
    inp = json.dumps({"issue_date": issue_date, "user_id": user_id})
    start = states.start_execution(
        stateMachineArn=sm_arn,
        name=name,
        input=inp,
    )
    arn = start["executionArn"]
    print(f"  [{issue_date}] Started FindContent: {arn}")
    for i in range(max_polls):
        d = states.describe_execution(executionArn=arn)
        st = d["status"]
        if st != "RUNNING":
            if st == "SUCCEEDED":
                print(f"  [{issue_date}] FindContent SUCCEEDED ({i + 1} polls)")
                return
            cause = d.get("cause", "")
            raise SystemExit(f"FindContent failed for {issue_date}: status={st} error={d.get('error')} cause={cause[:500]}")
        time.sleep(poll_seconds)
    raise SystemExit(f"FindContent timed out for {issue_date}: {arn}")


def ensure_fetch_rows(
    *,
    table,
    states,
    cfn,
    stack_name: str,
    pk: str,
    user_id: str,
    issue_date: str,
) -> None:
    n = count_fetch_prefix(table, pk, issue_date)
    if n > 0:
        print(f"  [{issue_date}] Dynamo already has {n} FETCH row(s); skip FindContent")
        return
    sm_arn = resolve_find_content_sm_arn(cfn, stack_name)
    run_find_content_and_wait(states, sm_arn=sm_arn, issue_date=issue_date, user_id=user_id)


def process_issue_date(
    table,
    s3,
    *,
    pk: str,
    user_id: str,
    issue_date: str,
    bucket: str,
    schema: str,
    dry_run: bool,
    force: bool,
    video_delay_seconds: float,
    transcript_max_attempts: int,
    transcript_initial_backoff_seconds: float,
    transcript_max_backoff_seconds: float,
    transcript_jitter_ratio: float,
    require_transcript: bool,
) -> int:
    sk_prefix = f"FETCH#{issue_date}#"
    query_kwargs: dict[str, Any] = {
        "KeyConditionExpression": "pk = :pk AND begins_with(sk, :skp)",
        "ExpressionAttributeValues": {":pk": pk, ":skp": sk_prefix},
    }
    items: list[dict[str, Any]] = []
    while True:
        resp = table.query(**query_kwargs)
        items.extend(resp.get("Items", []))
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
        query_kwargs["ExclusiveStartKey"] = lek

    processed_sources = 0
    for item in items:
        sk = item.get("sk", "")
        status = item.get("status", "")
        if status != "FETCHED":
            continue

        video_ids = _parse_video_ids(item)
        if not video_ids:
            continue

        source_id = _parse_source_id_from_sk(str(sk), item)
        prefix = transcript_s3_prefix(
            schema=schema,
            user_id=user_id,
            issue_date=issue_date,
            source_id=source_id,
        )

        print(f"\n== {issue_date} / {source_id} ({len(video_ids)} video(s)) prefix=s3://{bucket}/{prefix}")

        manifest_videos: list[dict[str, Any]] = []

        for video_id in video_ids:
            key = transcript_video_key(prefix, video_id)
            if not force and s3_object_exists(s3, bucket, key):
                print(f"  skip exists: s3://{bucket}/{key}")
                manifest_videos.append(
                    {
                        "video_id": video_id,
                        "s3_key": key,
                        "status": "skipped_exists",
                    }
                )
                continue

            text, label, err = fetch_transcript_text_with_retry(
                video_id,
                max_attempts=transcript_max_attempts,
                initial_backoff_seconds=transcript_initial_backoff_seconds,
                max_backoff_seconds=transcript_max_backoff_seconds,
                jitter_ratio=transcript_jitter_ratio,
            )
            if require_transcript and not text:
                raise SystemExit(
                    f"Transcript required but unavailable for {issue_date}/{source_id}/{video_id}. "
                    f"Last error: {err}"
                )
            payload = {
                "schema_version": 1,
                "user_id": user_id,
                "issue_date": issue_date,
                "source_id": source_id,
                "video_id": video_id,
                "video_url": f"https://www.youtube.com/watch?v={video_id}",
                "fetched_at": _utc_now_iso(),
                "transcript_source": label if text else "none",
                "transcript_error": err if not text else "",
                "transcript_text": text,
            }

            body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            print(
                f"  put s3://{bucket}/{key} ({len(body)} bytes, source={label or 'none'})"
                + (f" err={err}" if err and not text else "")
            )

            if not dry_run:
                s3.put_object(
                    Bucket=bucket,
                    Key=key,
                    Body=body,
                    ContentType="application/json; charset=utf-8",
                )

            manifest_videos.append(
                {
                    "video_id": video_id,
                    "s3_key": key,
                    "status": "uploaded" if text else "empty",
                    "transcript_source": label if text else "none",
                }
            )
            if video_delay_seconds > 0:
                time.sleep(video_delay_seconds)

        manifest = {
            "schema_version": 1,
            "user_id": user_id,
            "issue_date": issue_date,
            "source_id": source_id,
            "bucket": bucket,
            "prefix": prefix,
            "videos": manifest_videos,
            "updated_at": _utc_now_iso(),
        }
        mkey = manifest_key(prefix)
        mbody = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")
        print(f"  put manifest s3://{bucket}/{mkey} ({len(mbody)} bytes)")

        if not dry_run:
            s3.put_object(
                Bucket=bucket,
                Key=mkey,
                Body=mbody,
                ContentType="application/json; charset=utf-8",
            )

            table.update_item(
                Key={"pk": pk, "sk": sk},
                UpdateExpression=(
                    "SET transcript_s3_bucket = :b, transcript_s3_prefix = :p, "
                    "transcript_manifest_s3_key = :m, transcript_home_runner_at = :t"
                ),
                ExpressionAttributeValues={
                    ":b": bucket,
                    ":p": prefix,
                    ":m": mkey,
                    ":t": _utc_now_iso(),
                },
            )

        processed_sources += 1

    return processed_sources


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--region", default="us-east-1")
    p.add_argument("--table", required=True, help="DynamoDB table name (InfluencerFeedTable output)")
    p.add_argument("--bucket", required=True, help="TranscriptArtifactsBucket output")
    mx = p.add_mutually_exclusive_group(required=True)
    mx.add_argument(
        "--issue-date",
        help="Single issue date in America/New_York calendar form YYYY-MM-DD",
    )
    mx.add_argument(
        "--days-back",
        type=int,
        metavar="N",
        help="Process the last N America/New_York calendar days (oldest first)",
    )
    p.add_argument("--user-id", default="default")
    p.add_argument("--schema", default="v1", help="Key prefix version")
    p.add_argument("--force", action="store_true", help="Overwrite existing S3 transcript JSON")
    p.add_argument("--dry-run", action="store_true", help="Print actions without writing S3/Dynamo")
    p.add_argument(
        "--video-delay-seconds",
        type=float,
        default=2.5,
        help="Delay between transcript requests for videos (default: 2.5)",
    )
    p.add_argument(
        "--transcript-max-attempts",
        type=int,
        default=4,
        help="Max transcript attempts per video with backoff (default: 4)",
    )
    p.add_argument(
        "--transcript-initial-backoff-seconds",
        type=float,
        default=3.0,
        help="Initial backoff before transcript retry (default: 3.0)",
    )
    p.add_argument(
        "--transcript-max-backoff-seconds",
        type=float,
        default=45.0,
        help="Cap for exponential transcript retry backoff (default: 45.0)",
    )
    p.add_argument(
        "--transcript-jitter-ratio",
        type=float,
        default=0.35,
        help="Jitter ratio added to transcript retry sleeps (default: 0.35)",
    )
    p.add_argument(
        "--require-transcript",
        action="store_true",
        help="Fail fast when transcript text cannot be fetched for a video",
    )
    p.add_argument(
        "--allow-empty-transcript",
        action="store_true",
        help="Allow writing empty transcript artifacts when fetch fails",
    )
    p.add_argument("--proxy-http-url", default=None, help="Optional HTTP proxy URL for transcript requests")
    p.add_argument("--proxy-https-url", default=None, help="Optional HTTPS proxy URL for transcript requests")
    p.add_argument(
        "--proxy-list-file",
        default=None,
        help="Optional proxy list file (one per line): URL or host:port:username:password",
    )
    p.add_argument(
        "--webshare-proxy-username",
        default=None,
        help="Webshare proxy username (enables rotating proxy mode)",
    )
    p.add_argument(
        "--webshare-proxy-password",
        default=None,
        help="Webshare proxy password (enables rotating proxy mode)",
    )
    p.add_argument(
        "--webshare-filter-ip-locations",
        default=None,
        help="Optional comma-separated country codes for Webshare IP location filter (e.g. us,ca)",
    )
    p.add_argument(
        "--webshare-retries-when-blocked",
        type=int,
        default=10,
        help="Webshare blocked-request retries before failure (default: 10)",
    )
    p.add_argument(
        "--cookies-file",
        default=None,
        help="Optional Netscape-format cookie file for YouTube-authenticated transcript requests",
    )
    p.add_argument(
        "--ensure-find-content",
        action="store_true",
        help="If Dynamo has no FETCH# rows for a day, run FindContent and wait before transcripts",
    )
    p.add_argument(
        "--stack-name",
        default=None,
        help="CloudFormation stack name (required with --ensure-find-content)",
    )
    args = p.parse_args()

    if args.ensure_find_content and not args.stack_name:
        p.error("--stack-name is required when using --ensure-find-content")

    if args.days_back is not None and args.days_back < 1:
        p.error("--days-back must be >= 1")
    if args.transcript_max_attempts < 1:
        p.error("--transcript-max-attempts must be >= 1")
    if args.video_delay_seconds < 0:
        p.error("--video-delay-seconds must be >= 0")
    if args.webshare_retries_when_blocked < 0:
        p.error("--webshare-retries-when-blocked must be >= 0")
    if args.require_transcript and args.allow_empty_transcript:
        p.error("Use either --require-transcript or --allow-empty-transcript, not both")
    if args.proxy_list_file and (
        args.proxy_http_url
        or args.proxy_https_url
        or args.webshare_proxy_username
        or args.webshare_proxy_password
    ):
        p.error("Use --proxy-list-file by itself, or use Webshare/generic proxy flags")
    if (args.webshare_proxy_username or args.webshare_proxy_password) and (args.proxy_http_url or args.proxy_https_url):
        p.error("Use either Webshare proxy flags or generic proxy URL flags, not both")

    configure_transcript_client(args)
    require_transcript = True
    if args.allow_empty_transcript:
        require_transcript = False
    if args.require_transcript:
        require_transcript = True

    issue_dates = (
        [args.issue_date]
        if args.issue_date
        else et_issue_dates(args.days_back)
    )

    ddb = boto3.resource("dynamodb", region_name=args.region)
    table = ddb.Table(args.table)
    s3 = boto3.client("s3", region_name=args.region)
    states = boto3.client("stepfunctions", region_name=args.region)
    cfn = boto3.client("cloudformation", region_name=args.region)

    pk = f"USER#{args.user_id}"
    total_sources = 0

    print(
        f"Processing {len(issue_dates)} issue day(s) in America/New_York: "
        f"{issue_dates[0]} .. {issue_dates[-1]}"
    )

    for issue_date in issue_dates:
        print(f"\n--- {issue_date} ---")
        if args.ensure_find_content:
            ensure_fetch_rows(
                table=table,
                states=states,
                cfn=cfn,
                stack_name=args.stack_name,
                pk=pk,
                user_id=args.user_id,
                issue_date=issue_date,
            )
        n = process_issue_date(
            table,
            s3,
            pk=pk,
            user_id=args.user_id,
            issue_date=issue_date,
            bucket=args.bucket,
            schema=args.schema,
            dry_run=args.dry_run,
            force=args.force,
            video_delay_seconds=args.video_delay_seconds,
            transcript_max_attempts=args.transcript_max_attempts,
            transcript_initial_backoff_seconds=args.transcript_initial_backoff_seconds,
            transcript_max_backoff_seconds=args.transcript_max_backoff_seconds,
            transcript_jitter_ratio=args.transcript_jitter_ratio,
            require_transcript=require_transcript,
        )
        print(f"Done {issue_date}. Updated sources: {n}")
        total_sources += n

    print(f"\nAll done. Total source-day updates: {total_sources}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
