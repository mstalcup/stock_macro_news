"""Signal matrix + LLM pick performance blocks for Discord."""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

import boto3
from boto3.dynamodb.conditions import Key

# Bundled at deploy time from ../../signal_matrix/signal_matrix (see deploy.ps1)
_PKG_ROOT = Path(__file__).resolve().parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

try:
    from signal_matrix.context import build_context
    from signal_matrix.matrix import build_matrix
    from signal_matrix.types import ConfluenceTier
except ImportError:
    build_context = None  # type: ignore
    build_matrix = None  # type: ignore
    ConfluenceTier = None  # type: ignore


def _f(v) -> float | None:
    if v is None:
        return None
    if isinstance(v, Decimal):
        return float(v)
    return float(v)


def _chunk(text: str, limit: int = 1900) -> list[str]:
    t = (text or "").strip()
    if not t:
        return []
    out: list[str] = []
    while t:
        out.append(t[:limit])
        t = t[limit:].lstrip()
    return out


def _resolve_macro_bucket() -> str:
    env = (os.environ.get("MACRO_ARTIFACTS_BUCKET") or "").strip()
    if env:
        return env
    stack = (os.environ.get("MACRO_STACK_NAME") or "macro-news-feed").strip()
    profile = (os.environ.get("AWS_PROFILE") or "").strip()
    region = (os.environ.get("AWS_REGION") or "us-east-1").strip()
    session = boto3.Session(profile_name=profile) if profile else boto3.Session()
    cf = session.client("cloudformation", region_name=region)
    outs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cf.describe_stacks(StackName=stack)["Stacks"][0]["Outputs"]
    }
    return outs.get("NewsArtifactsBucket") or ""


def format_signal_matrix_messages(*, issue_date: str, slot: str = "pre_open") -> list[str]:
    if build_context is None or build_matrix is None:
        return _chunk(
            f"**Signal confluence — {issue_date}**\n_(matrix package not bundled; redeploy llm-sentiment-feed)_"
        )

    profile = (os.environ.get("AWS_PROFILE") or "").strip()
    ctx = build_context(
        issue_date=issue_date,
        slot=slot,
        macro_bucket=_resolve_macro_bucket(),
        profile=profile,
    )
    matrix = build_matrix(ctx)

    lines = [
        f"**Signal confluence — {issue_date}** (`{slot}`)",
        "_Macro watchlist + LLM panel + influencer consensus. L=long S=short._",
        "",
    ]
    hot = [
        r
        for r in matrix.rows
        if r.confluence
        and r.confluence.tier
        in (
            ConfluenceTier.UNANIMOUS,
            ConfluenceTier.STRONG,
            ConfluenceTier.LEAN,
        )
        and (r.confluence.long_channels + r.confluence.short_channels) > 0
    ]
    if not hot:
        lines.append("_No multi-channel alignment today._")
    else:
        for row in hot[:12]:
            c = row.confluence
            side = "LONG" if c.long_channels >= c.short_channels else "SHORT"
            lines.append(
                f"• **{row.ticker}** {side} — `{c.tier.value}` "
                f"({c.long_channels}L/{c.short_channels}S ch)"
            )

    errors = []
    for pr in matrix.provider_results:
        if pr.errors:
            errors.append(f"{pr.provider_id}: {pr.errors[0][:80]}")
    if errors:
        lines.extend(["", "_Provider notes:_ " + "; ".join(errors[:3])])

    return _chunk("\n".join(lines))


def _mtm_summary() -> dict | None:
    fn = (os.environ.get("SCORE_LAMBDA_NAME") or "llm-sentiment-feed-score-recommendations").strip()
    try:
        raw = (
            boto3.client("lambda")
            .invoke(FunctionName=fn, Payload=json.dumps({"mtm_report": True}).encode())
            .get("Payload")
            .read()
        )
        body = json.loads(raw)
        return body.get("mtm")
    except Exception as exc:
        print(f"mtm_report invoke failed: {exc!r}")
        return None


def format_llm_performance_messages(*, issue_date: str, table_name: str) -> list[str]:
    """Open picks with entry; includes live MTM when score lambda is reachable."""
    table = boto3.resource("dynamodb").Table(table_name)
    picks: list[dict] = []
    scan_kw: dict = {
        "FilterExpression": "begins_with(sk, :p)",
        "ExpressionAttributeValues": {":p": "PICK#"},
    }
    while True:
        r = table.scan(**scan_kw)
        picks.extend(r.get("Items", []))
        if "LastEvaluatedKey" not in r:
            break
        scan_kw["ExclusiveStartKey"] = r["LastEvaluatedKey"]

    rows = [p for p in picks if not str(p.get("sk", "")).startswith("test/")]
    official_7 = sum(1 for p in rows if p.get("return_7d") is not None)

    lines = [
        f"**LLM panel tracking — {issue_date}**",
        f"_Paper picks: {len(rows)} rows | official T+7 scored: {official_7}_",
        "",
    ]

    if not rows:
        lines.append("_No pick rows in DynamoDB yet._")
        return _chunk("\n".join(lines))

    # Per-model summary from picks with entry
    by_model: dict[str, list[dict]] = defaultdict(list)
    for p in rows:
        if p.get("entry_price") is None:
            continue
        by_model[str(p.get("model_id") or "unknown")].append(p)

    mtm = _mtm_summary()
    if mtm and mtm.get("all_models", {}).get("n"):
        am = mtm["all_models"]
        lines.append(
            f"**All models (MTM)** — avg `{am['avg_return_pct']:+.2f}%` "
            f"({am['wins']}/{am['n']} wins, {am['win_rate_pct']}%)"
        )
        for model, agg in (mtm.get("by_model") or {}).items():
            if agg.get("n"):
                lines.append(
                    f"• `{model}` MTM `{agg['avg_return_pct']:+.2f}%` "
                    f"({agg['wins']}/{agg['n']})"
                )
        lines.append("")

    if not by_model:
        lines.append("_Entries pending (same-day close fills after market close)._")
        return _chunk("\n".join(lines))

    for model in sorted(by_model):
        items = by_model[model]
        rets: list[float] = []
        wins = 0
        for p in items:
            r7 = p.get("return_7d")
            if r7 is not None:
                rv = _f(r7)
            else:
                rv = None
            if rv is not None:
                rets.append(rv)
                if rv > 0:
                    wins += 1
        if rets:
            lines.append(
                f"**{model}** — T+7: avg `{sum(rets)/len(rets):+.2f}%` "
                f"({wins}/{len(rets)} wins)"
            )
        else:
            lines.append(f"**{model}** — {len(items)} open (T+7 not due yet)")

    # Recent issue cohorts with any T+7
    by_issue: dict[str, list[float]] = defaultdict(list)
    for p in rows:
        r7 = p.get("return_7d")
        if r7 is None:
            continue
        by_issue[str(p.get("issue_date") or "")].append(_f(r7) or 0.0)

    if by_issue:
        lines.append("")
        lines.append("**T+7 by issue date**")
        for iss in sorted(by_issue)[-5:]:
            rs = by_issue[iss]
            w = sum(1 for x in rs if x > 0)
            lines.append(f"• `{iss}` avg `{sum(rs)/len(rs):+.2f}%` ({w}/{len(rs)})")

    return _chunk("\n".join(lines))
