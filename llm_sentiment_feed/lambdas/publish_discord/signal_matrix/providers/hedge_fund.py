"""
Placeholder for hedge-fund / 13F-style holdings.

Future integration ideas:
- Seed JSON or DynamoDB table: fund_id, ticker, position_type (long/short), as_of_date, weight
- Votes use kind='holding' (does not count toward trade confluence by default)
- Optional filter: only show holdings that overlap trade signals
"""
from __future__ import annotations

import json
from pathlib import Path

from ..normalize import normalize_direction, normalize_ticker
from ..types import MatrixContext, ProviderResult, SignalVote
from .base import SignalProvider


class HedgeFundHoldingsProvider(SignalProvider):
    provider_id = "hedge_fund"
    channel_id = "hedge_fund"
    description = "Static fund holdings (optional local seed file; AWS source TBD)"

    def load(self, ctx: MatrixContext) -> ProviderResult:
        votes: list[SignalVote] = []
        errors: list[str] = []
        meta: dict = {"status": "stub"}

        seed_path = ctx.extra.get("hedge_fund_seed")
        if not seed_path:
            meta["hint"] = (
                "Set ctx.extra['hedge_fund_seed'] to a JSON file, e.g. "
                "signal_matrix/seed/hedge_holdings.example.json"
            )
            return ProviderResult(self.provider_id, self.channel_id, votes, errors, meta)

        path = Path(seed_path)
        if not path.is_file():
            errors.append(f"seed file not found: {path}")
            return ProviderResult(self.provider_id, self.channel_id, votes, errors, meta)

        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            errors.append(f"invalid seed json: {exc!r}")
            return ProviderResult(self.provider_id, self.channel_id, votes, errors, meta)

        if not isinstance(rows, list):
            errors.append("seed must be a JSON array")
            return ProviderResult(self.provider_id, self.channel_id, votes, errors, meta)

        for row in rows:
            if not isinstance(row, dict):
                continue
            as_of = (row.get("as_of_date") or "").strip()
            if as_of and as_of > ctx.issue_date:
                continue
            ticker = normalize_ticker(row.get("ticker") or "")
            if not ticker:
                continue
            fund = (row.get("fund_id") or "unknown").strip()
            direction = normalize_direction(row.get("position") or row.get("direction") or "long")
            if direction is None:
                direction = "long"
            votes.append(
                SignalVote(
                    source_id=f"hedge:{fund}",
                    channel_id=self.channel_id,
                    ticker=ticker,
                    direction=direction,
                    kind="holding",
                    label=(row.get("position") or "holding").strip(),
                    weight=float(row.get("weight") or 1.0),
                    meta={
                        "fund_name": row.get("fund_name") or fund,
                        "as_of_date": as_of,
                        "source": row.get("source") or "seed",
                    },
                )
            )

        meta["holdings_loaded"] = len(votes)
        meta["seed_path"] = str(path)
        return ProviderResult(self.provider_id, self.channel_id, votes, errors, meta)
