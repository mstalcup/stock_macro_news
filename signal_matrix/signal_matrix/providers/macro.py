from __future__ import annotations

import json

from botocore.exceptions import ClientError

from ..context import aws_session
from ..normalize import normalize_direction, normalize_ticker
from ..types import MatrixContext, ProviderResult, SignalVote
from .base import SignalProvider


class MacroNewsProvider(SignalProvider):
    provider_id = "macro"
    channel_id = "macro"
    description = "Macro news digest ticker_watchlist (S3 digest.json)"

    def load(self, ctx: MatrixContext) -> ProviderResult:
        votes: list[SignalVote] = []
        errors: list[str] = []
        meta: dict = {"slot": ctx.slot}

        bucket = ctx.macro_bucket
        if not bucket:
            errors.append("macro_bucket not configured")
            return ProviderResult(self.provider_id, self.channel_id, votes, errors, meta)

        key = f"v1/date={ctx.issue_date}/slot={ctx.slot}/digest.json"
        s3 = aws_session(ctx).client("s3")
        try:
            body = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
            doc = json.loads(body)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404"):
                errors.append(f"missing s3://{bucket}/{key}")
            else:
                errors.append(f"s3 error: {exc!r}")
            return ProviderResult(self.provider_id, self.channel_id, votes, errors, meta)
        except json.JSONDecodeError as exc:
            errors.append(f"invalid digest json: {exc!r}")
            return ProviderResult(self.provider_id, self.channel_id, votes, errors, meta)

        digest = doc.get("digest") or {}
        meta["market_bias"] = digest.get("market_bias")
        meta["digest_key"] = key

        for row in digest.get("ticker_watchlist") or []:
            if not isinstance(row, dict):
                continue
            ticker = normalize_ticker(row.get("ticker") or "")
            if not ticker:
                continue
            bias = (row.get("bias") or "").strip()
            direction = normalize_direction(bias)
            votes.append(
                SignalVote(
                    source_id="macro:watchlist",
                    channel_id=self.channel_id,
                    ticker=ticker,
                    direction=direction,
                    kind="trade",
                    label=bias or "mixed",
                    meta={"note": (row.get("note") or "")[:200]},
                )
            )

        return ProviderResult(self.provider_id, self.channel_id, votes, errors, meta)
