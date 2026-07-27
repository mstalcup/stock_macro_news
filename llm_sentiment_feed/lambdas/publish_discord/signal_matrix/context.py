"""Resolve AWS resource names for matrix loads."""
from __future__ import annotations

import boto3

from .types import MatrixContext


def aws_session(ctx: MatrixContext) -> boto3.Session:
    """Lambda uses the execution role; local CLI may pass AWS_PROFILE / --profile."""
    profile = (ctx.profile or "").strip()
    if profile:
        return boto3.Session(profile_name=profile, region_name=ctx.region)
    return boto3.Session(region_name=ctx.region)


def resolve_macro_bucket(ctx: MatrixContext) -> str:
    if ctx.macro_bucket:
        return ctx.macro_bucket
    cf = aws_session(ctx).client("cloudformation")
    outs = {
        o["OutputKey"]: o["OutputValue"]
        for o in cf.describe_stacks(StackName=ctx.macro_stack)["Stacks"][0]["Outputs"]
    }
    return outs.get("NewsArtifactsBucket") or ""


def build_context(
    *,
    issue_date: str,
    slot: str = "pre_open",
    profile: str = "",
    region: str = "us-east-1",
    influencer_user_id: str = "default",
    macro_bucket: str = "",
    macro_stack: str = "macro-news-feed",
    llm_stack: str = "llm-sentiment-feed",
    influencer_stack: str = "influencer-feed",
    extra: dict | None = None,
) -> MatrixContext:
    ctx = MatrixContext(
        issue_date=issue_date,
        slot=slot,
        profile=profile,
        region=region,
        influencer_user_id=influencer_user_id,
        macro_bucket=macro_bucket,
        macro_stack=macro_stack,
        llm_stack=llm_stack,
        influencer_stack=influencer_stack,
        extra=extra or {},
    )
    if not ctx.macro_bucket:
        ctx.macro_bucket = resolve_macro_bucket(ctx)
    return ctx
