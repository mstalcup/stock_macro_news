from __future__ import annotations

from boto3.dynamodb.conditions import Key

from ..context import aws_session
from ..normalize import normalize_direction, normalize_ticker
from ..types import MatrixContext, ProviderResult, SignalVote
from .base import SignalProvider

ACTIVE_MODELS = ("openai-gpt-4o", "gemini-2.5-flash", "grok-4.3")


class LlmSentimentProvider(SignalProvider):
    provider_id = "llm_sentiment"
    channel_id = "llm_sentiment"
    description = "LLM sentiment panel picks (DynamoDB ISSUE#date / MODEL#*)"

    def _table_name(self, ctx: MatrixContext) -> str:
        if ctx.extra.get("llm_table"):
            return str(ctx.extra["llm_table"])
        cf = aws_session(ctx).client("cloudformation")
        outs = {
            o["OutputKey"]: o["OutputValue"]
            for o in cf.describe_stacks(StackName=ctx.llm_stack)["Stacks"][0]["Outputs"]
        }
        return outs["SentimentTableName"]

    def load(self, ctx: MatrixContext) -> ProviderResult:
        votes: list[SignalVote] = []
        errors: list[str] = []
        meta: dict = {}

        try:
            table = aws_session(ctx).resource("dynamodb").Table(self._table_name(ctx))
        except Exception as exc:
            errors.append(f"dynamodb: {exc!r}")
            return ProviderResult(self.provider_id, self.channel_id, votes, errors, meta)

        pk = f"ISSUE#{ctx.issue_date}"
        resp = table.query(KeyConditionExpression=Key("pk").eq(pk))
        items = [i for i in resp.get("Items", []) if str(i.get("sk", "")).startswith("MODEL#")]

        by_model: dict[str, dict] = {}
        for item in items:
            mid = item.get("model_id") or ""
            if mid not in ACTIVE_MODELS:
                continue
            prev = by_model.get(mid)
            if not prev:
                by_model[mid] = item
                continue
            if item.get("status") == "ok" and prev.get("status") != "ok":
                by_model[mid] = item
            elif (item.get("queried_at") or "") > (prev.get("queried_at") or ""):
                by_model[mid] = item

        for mid, item in by_model.items():
            if item.get("status") != "ok":
                errors.append(f"{mid}: status={item.get('status')}")
                continue
            meta[f"{mid}_bias"] = item.get("market_bias")
            for p in item.get("picks") or []:
                if not isinstance(p, dict):
                    continue
                ticker = normalize_ticker(p.get("ticker") or "")
                if not ticker:
                    continue
                raw_dir = (p.get("direction") or "long").strip().lower()
                direction = normalize_direction(raw_dir)
                if direction is None:
                    direction = "long" if raw_dir == "long" else "short"
                votes.append(
                    SignalVote(
                        source_id=f"llm:{mid}",
                        channel_id=self.channel_id,
                        ticker=ticker,
                        direction=direction,
                        kind="trade",
                        label=raw_dir,
                        meta={
                            "conviction": p.get("conviction"),
                            "rationale": (p.get("rationale") or "")[:160],
                        },
                    )
                )

        meta["models_loaded"] = sorted(by_model.keys())
        return ProviderResult(self.provider_id, self.channel_id, votes, errors, meta)
