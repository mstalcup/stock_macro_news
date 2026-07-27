from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import boto3

TABLE_NAME = os.environ["SENTIMENT_TABLE_NAME"]
ARTIFACTS_BUCKET = os.environ["SENTIMENT_ARTIFACTS_BUCKET"]


def _prefix() -> str:
    if (os.environ.get("TEST_RUN") or "").strip().lower() in ("1", "true", "yes"):
        return "test/"
    return ""


def issue_pk(issue_date: str) -> str:
    return f"{_prefix()}ISSUE#{issue_date}"


def model_sk(model_id: str) -> str:
    return f"MODEL#{model_id}"


def pick_sk(issue_date: str, model_id: str, ticker: str) -> str:
    return f"{_prefix()}PICK#{issue_date}#{model_id}#{ticker}"


def put_model_result(
    *,
    issue_date: str,
    model_id: str,
    provider: str,
    prompt_version: str,
    use_context: bool,
    result: dict,
    raw_response: str,
    context_meta: dict,
    error: str | None = None,
) -> dict:
    s3 = boto3.client("s3")
    table = boto3.resource("dynamodb").Table(TABLE_NAME)
    now = datetime.now(timezone.utc).isoformat()
    pfx = _prefix()
    raw_key = f"{pfx}v1/issue_date={issue_date}/model={model_id}/response.json"

    s3_body = {
        "schema_version": 1,
        "issue_date": issue_date,
        "model_id": model_id,
        "provider": provider,
        "prompt_version": prompt_version,
        "use_context": use_context,
        "queried_at": now,
        "result": result,
        "raw_response": raw_response[:50000] if raw_response else "",
        "error": error,
        "context_meta": context_meta,
    }
    s3.put_object(
        Bucket=ARTIFACTS_BUCKET,
        Key=raw_key,
        Body=json.dumps(s3_body, ensure_ascii=False, indent=2).encode("utf-8"),
        ContentType="application/json; charset=utf-8",
    )

    item = {
        "pk": issue_pk(issue_date),
        "sk": model_sk(model_id),
        "issue_date": issue_date,
        "model_id": model_id,
        "provider": provider,
        "prompt_version": prompt_version,
        "use_context": use_context,
        "market_bias": result.get("market_bias"),
        "picks": result.get("picks") or [],
        "pick_count": len(result.get("picks") or []),
        "raw_s3_key": raw_key,
        "context_meta": context_meta,
        "queried_at": now,
        "status": "error" if error else "ok",
        "error": error,
        "test_run": bool(pfx),
    }
    table.put_item(Item=item)

    for pick in result.get("picks") or []:
        ticker = pick.get("ticker")
        if not ticker:
            continue
        table.put_item(
            Item={
                "pk": f"{pfx}TICKER#{ticker}",
                "sk": pick_sk(issue_date, model_id, ticker),
                "issue_date": issue_date,
                "model_id": model_id,
                "ticker": ticker,
                "direction": pick.get("direction"),
                "conviction": pick.get("conviction"),
                "themes": pick.get("themes") or [],
                "rationale": pick.get("rationale"),
                "catalysts": pick.get("catalysts"),
                "entry_date": issue_date,
                "entry_price": None,
                "entry_status": "pending_close",
                "exit_7d_price": None,
                "exit_30d_price": None,
                "return_7d": None,
                "return_30d": None,
                "queried_at": now,
                "test_run": bool(pfx),
            }
        )

    return {"raw_s3_key": raw_key, "pick_count": item["pick_count"]}
