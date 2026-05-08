"""
Rough leaderboard: for ISSUE_SOURCE rows in a date range, map LLM ticker directions
to Yahoo Finance daily returns (1 and 3 trading sessions after the first bar on
or after issue_date in America/Los_Angeles).

This is an experiment — not investment advice. Skips mixed/unclear/neutral calls.

Examples:
  py tools/score_quick_returns.py --days 7 --users default,crypto
  py tools/score_quick_returns.py --start 2026-04-28 --end 2026-05-06 --users crypto
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.parse
import urllib.request
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import boto3


def _stack_output(cf, stack: str, key: str) -> str:
    stacks = cf.describe_stacks(StackName=stack)["Stacks"]
    outs = {o["OutputKey"]: o["OutputValue"] for o in stacks[0].get("Outputs", [])}
    val = outs.get(key)
    if not val:
        raise SystemExit(f"Stack {stack} has no output {key}")
    return val


def _to_plain(v: Any) -> Any:
    if isinstance(v, Decimal):
        return int(v) if v % 1 == 0 else float(v)
    if isinstance(v, dict):
        return {k: _to_plain(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_to_plain(x) for x in v]
    return v


def _yahoo_symbol(raw: str) -> str | None:
    t = (raw or "").strip().upper().replace("$", "")
    if not t:
        return None
    aliases = {
        "BTC": "BTC-USD",
        "BITCOIN": "BTC-USD",
        "ETH": "ETH-USD",
        "ETHEREUM": "ETH-USD",
        "SOL": "SOL-USD",
        "XRP": "XRP-USD",
        "DOGE": "DOGE-USD",
    }
    if t in aliases:
        return aliases[t]
    if "-" in t and t.endswith("-USD"):
        return t
    return t


def _bear(d: str) -> bool:
    x = d.strip().lower()
    return x in ("bearish", "bear", "short", "sell", "negative")


def _bull(d: str) -> bool:
    x = d.strip().lower()
    return x in ("bullish", "bull", "long", "buy", "positive")


def _chart_closes(symbol: str, p1: int, p2: int) -> tuple[list[int], list[float]] | None:
    sym = urllib.parse.quote(symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
        f"?period1={p1}&period2={p2}&interval=1d"
    )
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "stock-macro-news-scorer/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return None
    res = (data.get("chart") or {}).get("result")
    if not res:
        return None
    r0 = res[0]
    ts = r0.get("timestamp") or []
    q = (r0.get("indicators") or {}).get("quote") or [{}]
    closes = (q[0].get("close") if q else None) or []
    out_ts: list[int] = []
    out_c: list[float] = []
    for i, t in enumerate(ts):
        if i >= len(closes):
            break
        c = closes[i]
        if c is None:
            continue
        out_ts.append(int(t))
        out_c.append(float(c))
    if len(out_ts) < 2:
        return None
    return out_ts, out_c


def _la_anchor_index(timestamps: list[int], issue: date) -> int:
    from zoneinfo import ZoneInfo

    LA = ZoneInfo("America/Los_Angeles")
    for i, ts in enumerate(timestamps):
        bar_d = datetime.fromtimestamp(ts, tz=LA).date()
        if bar_d >= issue:
            return i
    return -1


def _hit(ret: float, direction: str) -> bool | None:
    if _bull(direction):
        return ret > 0
    if _bear(direction):
        return ret < 0
    return None


def _daterange(start: date, end: date) -> Iterable[date]:
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--profile", default="mastalcup")
    ap.add_argument("--region", default="us-east-1")
    ap.add_argument("--stack", default="influencer-feed")
    ap.add_argument("--days", type=int, default=7, help="If --start/--end not set, last N days (LA)")
    ap.add_argument("--start", default="", help="YYYY-MM-DD inclusive")
    ap.add_argument("--end", default="", help="YYYY-MM-DD inclusive")
    ap.add_argument("--users", default="default,crypto")
    args = ap.parse_args()

    from zoneinfo import ZoneInfo

    LA = ZoneInfo("America/Los_Angeles")

    if args.start and args.end:
        start_d = date.fromisoformat(args.start)
        end_d = date.fromisoformat(args.end)
    else:
        today_la = datetime.now(LA).date()
        end_d = today_la
        start_d = end_d - timedelta(days=args.days - 1)
    date_strings = [d.isoformat() for d in _daterange(start_d, end_d)]

    users = [u.strip() for u in args.users.split(",") if u.strip()]
    sess = boto3.Session(profile_name=args.profile, region_name=args.region)
    cf = sess.client("cloudformation")
    table_name = _stack_output(cf, args.stack, "InfluencerFeedTable")
    table = sess.resource("dynamodb").Table(table_name)

    # stats[(user_id, source_id)] = {"n":, "hit1":, "hit3":}
    stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"n1": 0, "h1": 0, "n3": 0, "h3": 0})

    for user_id in users:
        pk = f"USER#{user_id}"
        for issue_s in date_strings:
            pref = f"ISSUE_SOURCE#{issue_s}#"
            kwargs = {
                "KeyConditionExpression": "pk = :pk AND begins_with(sk, :p)",
                "ExpressionAttributeValues": {":pk": pk, ":p": pref},
            }
            while True:
                resp = table.query(**kwargs)
                for it in resp.get("Items", []):
                    it = _to_plain(it)
                    sk = it.get("sk", "")
                    parts = sk.split("#")
                    if len(parts) < 3:
                        continue
                    source_id = parts[2]
                    if it.get("status") != "FETCHED":
                        continue
                    tickers = it.get("source_tickers") or []
                    if not isinstance(tickers, list):
                        continue
                    issue = date.fromisoformat(issue_s)
                    p1 = int(datetime.combine(issue - timedelta(days=7), datetime.min.time()).replace(tzinfo=LA).timestamp())
                    p2 = int(datetime.combine(issue + timedelta(days=30), datetime.min.time()).replace(tzinfo=LA).timestamp())
                    for t in tickers:
                        if not isinstance(t, dict):
                            continue
                        sym_raw = (t.get("ticker") or "").strip()
                        direction = (t.get("direction") or "").strip() or "unclear"
                        if not (_bull(direction) or _bear(direction)):
                            continue
                        y = _yahoo_symbol(sym_raw)
                        if not y:
                            continue
                        chart = _chart_closes(y, p1, p2)
                        time.sleep(0.15)
                        if not chart:
                            continue
                        ts_list, closes = chart
                        ix = _la_anchor_index(ts_list, issue)
                        if ix < 0 or ix + 1 >= len(closes):
                            continue
                        c0 = closes[ix]
                        r1 = (closes[ix + 1] - c0) / c0 if ix + 1 < len(closes) else None
                        r3 = None
                        if ix + 3 < len(closes):
                            r3 = (closes[ix + 3] - c0) / c0
                        key = (user_id, source_id)
                        if r1 is not None:
                            hit = _hit(r1, direction)
                            if hit is not None:
                                stats[key]["n1"] += 1
                                if hit:
                                    stats[key]["h1"] += 1
                        if r3 is not None:
                            hit3 = _hit(r3, direction)
                            if hit3 is not None:
                                stats[key]["n3"] += 1
                                if hit3:
                                    stats[key]["h3"] += 1
                lek = resp.get("LastEvaluatedKey")
                if not lek:
                    break
                kwargs["ExclusiveStartKey"] = lek

    rows_out = []
    for (uid, sid), s in stats.items():
        n1, h1, n3, h3 = s["n1"], s["h1"], s["n3"], s["h3"]
        rows_out.append(
            {
                "user_id": uid,
                "source_id": sid,
                "n_1d": n1,
                "hit_1d": h1,
                "rate_1d": (h1 / n1) if n1 else None,
                "n_3d": n3,
                "hit_3d": h3,
                "rate_3d": (h3 / n3) if n3 else None,
            }
        )
    rows_out.sort(key=lambda r: (r["rate_1d"] is not None, r["rate_1d"] or 0, r["n_1d"]), reverse=True)

    print(json.dumps({"start": start_d.isoformat(), "end": end_d.isoformat(), "rows": rows_out}, indent=2))


if __name__ == "__main__":
    main()
