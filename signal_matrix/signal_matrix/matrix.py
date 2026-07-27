"""Build cross-feed signal matrix and confluence scores."""
from __future__ import annotations

from collections import defaultdict

from .normalize import direction_arrow
from .registry import resolve_providers
from .types import (
    ChannelRollup,
    ConfluenceScore,
    ConfluenceTier,
    MatrixContext,
    ProviderResult,
    SignalCell,
    SignalMatrix,
    SignalVote,
    TickerMatrixRow,
)


def _rollup_channel(votes: list[SignalVote], channel_id: str) -> ChannelRollup:
    r = ChannelRollup(channel_id=channel_id)
    for v in votes:
        if v.channel_id != channel_id:
            continue
        if v.kind == "holding":
            if v.direction == "long":
                r.holding_long += 1
            elif v.direction == "short":
                r.holding_short += 1
            else:
                r.abstain += 1
            continue
        if v.direction == "long":
            r.trade_long += 1
        elif v.direction == "short":
            r.trade_short += 1
        else:
            r.abstain += 1
    return r


def score_confluence(
    channels: dict[str, ChannelRollup],
    *,
    include_holdings_note: bool = True,
) -> ConfluenceScore:
    """Score trade-signal overlap across channels (holdings are informational)."""
    long_ch = short_ch = 0
    trade_long = trade_short = 0
    holding_bits: list[str] = []

    for cid, ch in channels.items():
        if cid == "hedge_fund":
            if ch.holding_long or ch.holding_short:
                hdir = "L" if ch.holding_long >= ch.holding_short else "S"
                holding_bits.append(f"{hdir} holding")
            continue
        d = ch.trade_direction
        if d == "long":
            long_ch += 1
            trade_long += ch.trade_long
        elif d == "short":
            short_ch += 1
            trade_short += ch.trade_short

    total_trade_channels = long_ch + short_ch
    holding_note = ", ".join(holding_bits) if include_holdings_note and holding_bits else ""

    if total_trade_channels == 0:
        return ConfluenceScore(
            tier=ConfluenceTier.NONE,
            score=0.0,
            holding_note=holding_note,
        )

    if long_ch > 0 and short_ch > 0:
        tier = ConfluenceTier.SPLIT
        score = 0.25
    elif total_trade_channels == 1:
        tier = ConfluenceTier.SOLO
        score = 0.35
    elif total_trade_channels == 2:
        tier = ConfluenceTier.LEAN
        score = 0.55
    elif total_trade_channels >= 3 and (long_ch == total_trade_channels or short_ch == total_trade_channels):
        tier = ConfluenceTier.UNANIMOUS
        score = 0.95
    else:
        tier = ConfluenceTier.STRONG
        score = 0.75

    return ConfluenceScore(
        tier=tier,
        score=score,
        long_channels=long_ch,
        short_channels=short_ch,
        trade_votes_long=trade_long,
        trade_votes_short=trade_short,
        holding_note=holding_note,
    )


def build_matrix(ctx: MatrixContext, *, provider_ids: list[str] | None = None) -> SignalMatrix:
    providers = resolve_providers(provider_ids)
    results: list[ProviderResult] = []
    all_votes: list[SignalVote] = []
    context_meta: dict = {"providers_requested": provider_ids or []}

    for prov in providers:
        pr = prov.load(ctx)
        results.append(pr)
        all_votes.extend(pr.votes)
        context_meta[pr.provider_id] = {"meta": pr.meta, "errors": pr.errors, "vote_count": len(pr.votes)}

    by_ticker: dict[str, list[SignalVote]] = defaultdict(list)
    for v in all_votes:
        by_ticker[v.ticker].append(v)

    channel_ids = sorted({v.channel_id for v in all_votes})

    rows: list[TickerMatrixRow] = []
    for ticker in sorted(by_ticker):
        tv = by_ticker[ticker]
        cells: dict[str, SignalCell] = {}
        for v in tv:
            cells[v.source_id] = SignalCell(
                source_id=v.source_id,
                channel_id=v.channel_id,
                direction=v.direction,
                label=v.label,
                kind=v.kind,
                meta=v.meta,
            )
        channels = {cid: _rollup_channel(tv, cid) for cid in channel_ids}
        conf = score_confluence(channels)
        rows.append(TickerMatrixRow(ticker=ticker, cells=cells, channels=channels, confluence=conf))

    rows.sort(
        key=lambda r: (
            -(r.confluence.score if r.confluence else 0),
            r.ticker,
        )
    )

    return SignalMatrix(
        issue_date=ctx.issue_date,
        slot=ctx.slot,
        provider_results=results,
        rows=rows,
        context_meta=context_meta,
    )


def matrix_to_dict(matrix: SignalMatrix) -> dict:
    """JSON-serializable summary."""
    return {
        "issue_date": matrix.issue_date,
        "slot": matrix.slot,
        "context_meta": matrix.context_meta,
        "providers": [
            {
                "provider_id": pr.provider_id,
                "channel_id": pr.channel_id,
                "vote_count": len(pr.votes),
                "errors": pr.errors,
                "meta": pr.meta,
            }
            for pr in matrix.provider_results
        ],
        "rows": [
            {
                "ticker": row.ticker,
                "confluence": {
                    "tier": row.confluence.tier.value if row.confluence else "none",
                    "score": row.confluence.score if row.confluence else 0,
                    "long_channels": row.confluence.long_channels if row.confluence else 0,
                    "short_channels": row.confluence.short_channels if row.confluence else 0,
                    "holding_note": row.confluence.holding_note if row.confluence else "",
                },
                "cells": {
                    sid: {
                        "direction": c.direction,
                        "label": c.label,
                        "kind": c.kind,
                        "channel_id": c.channel_id,
                    }
                    for sid, c in row.cells.items()
                },
            }
            for row in matrix.rows
        ],
    }
