"""Poll EDGAR for anticipatory filings (13D/G) and post to Discord. No 13F headline alerts."""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import boto3
from botocore.exceptions import ClientError

from whalelib.alert_policy import should_alert
from whalelib.backtest_config import ALERT_CONFIDENCE_IMMEDIATE, EDGAR_USER_AGENT
from whalelib.edgar import (
    build_name_ticker_map,
    company_tickers_map,
    enrich_schedule_filing,
    parse_schedule_entities,
    recent_filings_for_cik,
    search_filings,
)

ET = ZoneInfo("America/New_York")
DISCORD_API = "https://discord.com/api/v10"
TABLE_NAME = (os.environ.get("WHALE_TABLE_NAME") or "").strip()
DISCORD_BOT_SECRET_ARN = (os.environ.get("DISCORD_BOT_SECRET_ARN") or "").strip()
CHANNEL_ID_ENV = (os.environ.get("WHALE_DISCORD_CHANNEL_ID") or "").strip()
MSG_LIMIT = 1900

PRIMARY_TYPES = frozenset({"13d_new", "13d_increase", "13g_new", "13g_increase"})
CONFIDENCE = {"13d_new": 90, "13d_increase": 85, "13g_new": 75, "13g_increase": 70}


def _floats_to_decimal(obj):
    if isinstance(obj, float):
        return Decimal(str(obj))
    if isinstance(obj, dict):
        return {k: _floats_to_decimal(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_floats_to_decimal(i) for i in obj]
    return obj


def _load_roster_ciks() -> dict[str, dict]:
    """Roster from Dynamo FUND# or env JSON path fallback."""
    out: dict[str, dict] = {}
    if TABLE_NAME:
        table = boto3.resource("dynamodb").Table(TABLE_NAME)
        resp = table.scan(FilterExpression="begins_with(pk, :p)", ExpressionAttributeValues={":p": "FUND#"})
        for item in resp.get("Items", []):
            cik = str(item.get("cik") or "").lstrip("0")
            if cik:
                out[cik] = item
    return out


def _get_bot() -> tuple[str, str]:
    if not DISCORD_BOT_SECRET_ARN:
        return "", ""
    raw = (
        boto3.client("secretsmanager")
        .get_secret_value(SecretId=DISCORD_BOT_SECRET_ARN)
        .get("SecretString")
        or ""
    ).strip()
    if not raw:
        return "", ""
    data = json.loads(raw)
    return (data.get("bot_token") or "").strip(), str(data.get("channel_id") or CHANNEL_ID_ENV or "").strip()


def _chunk(text: str, limit: int = MSG_LIMIT) -> list[str]:
    t = (text or "").strip()
    if not t or len(t) <= limit:
        return [t] if t else []
    out: list[str] = []
    while t:
        if len(t) <= limit:
            out.append(t)
            break
        cut = t.rfind("\n", 0, limit)
        if cut < limit // 3:
            cut = limit
        out.append(t[:cut].rstrip())
        t = t[cut:].lstrip()
    return out


def _post(token: str, channel_id: str, content: str) -> None:
    data = json.dumps({"content": content}).encode("utf-8")
    req = urllib.request.Request(
        f"{DISCORD_API}/channels/{channel_id}/messages",
        data=data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bot {token}",
            "User-Agent": EDGAR_USER_AGENT,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=25) as resp:
        resp.read()


def _format_alert(hit: dict, roster: dict[str, dict]) -> str:
    fund = hit.get("filer_name") or hit.get("filer_cik") or "?"
    ticker = hit.get("issuer_ticker") or "?"
    st = hit.get("signal_type") or "filing"
    conf = CONFIDENCE.get(st, 70)
    tier = (roster.get(str(hit.get("filer_cik", "")).lstrip("0"), {}) or {}).get("tier", "")
    tier_s = f" · tier `{tier}`" if tier else ""
    url = hit.get("source_url") or f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={hit.get('filer_cik')}"
    label = st.replace("_", " ").upper()
    return (
        f"**Whale alert — {ticker}** · **{fund}**{tier_s}\n"
        f"Anticipatory · `{label}` · confidence **{conf}**\n"
        f"_Pre-13F signal — not a 13F filing headline._\n"
        f"{url}"
    )


def _seen_key(hit: dict) -> str:
    return f"SEEN#{hit.get('accession_dashed') or hit.get('accession')}#{hit.get('issuer_ticker') or ''}"


def _mark_seen(hit: dict) -> bool:
    if not TABLE_NAME:
        return False
    pk = _seen_key(hit)
    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    try:
        table.put_item(
            Item=_floats_to_decimal(
                {
                    "pk": pk,
                    "sk": "META",
                    "seen_at": datetime.now(tz=ET).isoformat(),
                    "file_date": hit.get("file_date"),
                }
            ),
            ConditionExpression="attribute_not_exists(pk)",
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "ConditionalCheckFailedException":
            return False
        raise


def _already_seen(hit: dict) -> bool:
    if not TABLE_NAME:
        return False
    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    return "Item" in table.get_item(Key={"pk": _seen_key(hit), "sk": "META"})


def handler(event, context):
    event = event or {}
    if (os.environ.get("SKIP_DISCORD_PUBLISH") or "").lower() in ("1", "true", "yes"):
        skip_discord = True
    else:
        skip_discord = False

    lookback_days = int(event.get("lookback_days") or 3)
    end = date.today()
    start = end - timedelta(days=lookback_days)

    roster = _load_roster_ciks()
    cik_map = company_tickers_map()
    name_map = build_name_ticker_map()

    schedule_forms = {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}
    hits: list[dict] = []
    roster_ciks = list(roster.keys())
    for cik in roster_ciks:
        hits.extend(recent_filings_for_cik(cik, forms=schedule_forms, limit=15))
    # Global search catches funds not yet on roster
    hits.extend(
        search_filings(
            forms=list(schedule_forms),
            start_date=start,
            end_date=end,
            max_pages=2,
        )
    )
    # Dedupe by accession
    seen_acc: set[str] = set()
    deduped: list[dict] = []
    for h in hits:
        acc = h.get("accession_dashed") or h.get("accession") or ""
        if acc in seen_acc:
            continue
        seen_acc.add(acc)
        deduped.append(h)
    hits = deduped

    alerts: list[dict] = []
    for hit in hits:
        enriched = enrich_schedule_filing(parse_schedule_entities(hit), cik_map, name_map)
        if enriched.get("signal_type") not in PRIMARY_TYPES:
            continue
        if not enriched.get("issuer_ticker"):
            continue
        if enriched.get("filer_cik") == enriched.get("issuer_cik"):
            continue
        fund_meta = roster.get(str(enriched.get("filer_cik", "")).lstrip("0"), {}) or {}
        if not should_alert(enriched, tier=str(fund_meta.get("tier") or "")):
            continue
        if _already_seen(enriched):
            continue
        conf = CONFIDENCE.get(enriched.get("signal_type", ""), 0)
        if conf < ALERT_CONFIDENCE_IMMEDIATE:
            continue
        if _mark_seen(enriched):
            alerts.append(enriched)

    messages = [_format_alert(a, roster) for a in alerts]
    if not messages and not skip_discord:
        messages = [
            f"**Whale scan — {end.isoformat()}** · no new anticipatory 13D/G on roster window ({lookback_days}d)."
        ]

    status = "ok"
    if not skip_discord:
        token, channel_id = _get_bot()
        if token and channel_id:
            try:
                for msg in messages:
                    for part in _chunk(msg):
                        _post(token, channel_id, part)
                status = "posted_bot"
            except (urllib.error.URLError, urllib.error.HTTPError) as exc:
                print(f"discord failed: {exc!r}")
                status = "discord_error"
        else:
            status = "no_credentials"

    return {
        "scan_date": end.isoformat(),
        "hits_scanned": len(hits),
        "alerts_new": len(alerts),
        "discord_status": status,
    }
