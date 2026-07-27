from __future__ import annotations

from boto3.dynamodb.conditions import Key

from ..context import aws_session
from ..normalize import normalize_direction, normalize_ticker
from ..types import MatrixContext, ProviderResult, SignalVote
from .base import SignalProvider


class InfluencerFeedProvider(SignalProvider):
    provider_id = "influencer"
    channel_id = "influencer"
    description = "Influencer per-source tickers + global_ticker_focus consensus"

    def _table_name(self, ctx: MatrixContext) -> str:
        if ctx.extra.get("influencer_table"):
            return str(ctx.extra["influencer_table"])
        cf = aws_session(ctx).client("cloudformation")
        outs = {
            o["OutputKey"]: o["OutputValue"]
            for o in cf.describe_stacks(StackName=ctx.influencer_stack)["Stacks"][0]["Outputs"]
        }
        return outs["InfluencerFeedTable"]

    def load(self, ctx: MatrixContext) -> ProviderResult:
        votes: list[SignalVote] = []
        errors: list[str] = []
        meta: dict = {"user_id": ctx.influencer_user_id}

        pk = f"USER#{ctx.influencer_user_id}"
        try:
            table = aws_session(ctx).resource("dynamodb").Table(self._table_name(ctx))
        except Exception as exc:
            errors.append(f"dynamodb: {exc!r}")
            return ProviderResult(self.provider_id, self.channel_id, votes, errors, meta)

        issue = table.get_item(Key={"pk": pk, "sk": f"ISSUE#{ctx.issue_date}"}).get("Item") or {}
        meta["global_summary_present"] = bool(issue.get("global_summary_smol"))

        for row in issue.get("global_ticker_focus") or []:
            if not isinstance(row, dict):
                continue
            ticker = normalize_ticker(row.get("ticker") or "")
            if not ticker:
                continue
            consensus = (row.get("consensus") or "mixed").strip().lower()
            direction = normalize_direction(consensus)
            votes.append(
                SignalVote(
                    source_id="influencer:consensus",
                    channel_id=self.channel_id,
                    ticker=ticker,
                    direction=direction,
                    kind="trade",
                    label=consensus,
                    meta={"note": (row.get("note") or "")[:200], "aggregate": "global"},
                )
            )

        pref = f"ISSUE_SOURCE#{ctx.issue_date}#"
        kwargs = {
            "KeyConditionExpression": Key("pk").eq(pk) & Key("sk").begins_with(pref),
        }
        source_count = 0
        while True:
            resp = table.query(**kwargs)
            for it in resp.get("Items", []):
                sk = str(it.get("sk", ""))
                parts = sk.split("#")
                if len(parts) < 3:
                    continue
                source_id = parts[2]
                if it.get("status") != "FETCHED":
                    continue
                source_count += 1
                for t in it.get("source_tickers") or []:
                    if not isinstance(t, dict):
                        continue
                    ticker = normalize_ticker(t.get("ticker") or "")
                    if not ticker:
                        continue
                    raw = (t.get("direction") or "unclear").strip().lower()
                    direction = normalize_direction(raw)
                    votes.append(
                        SignalVote(
                            source_id=f"influencer:{source_id}",
                            channel_id=self.channel_id,
                            ticker=ticker,
                            direction=direction,
                            kind="trade",
                            label=raw,
                            meta={
                                "display_name": it.get("display_name") or source_id,
                                "note": (t.get("note") or "")[:120],
                            },
                        )
                    )
            lek = resp.get("LastEvaluatedKey")
            if not lek:
                break
            kwargs["ExclusiveStartKey"] = lek

        meta["sources_with_fetch"] = source_count
        return ProviderResult(self.provider_id, self.channel_id, votes, errors, meta)
