"""
run_local.py — Run the full Market Pulse pipeline locally without AWS.

Usage:
    pip install -r requirements.txt
    export ALPHA_VANTAGE_API_KEY=...
    export NEWS_API_KEY=...
    export ANTHROPIC_API_KEY=...
    export DISCORD_WEBHOOK_URL=...  # optional
    export LOCAL_MODE=true          # skips DynamoDB, prints to stdout
    python run_local.py

Optional flags:
    --date 2025-01-15   # Run for a specific date
    --skip-news         # Skip API news fetches (use dummy data)
    --skip-discord      # Don't post to Discord even if webhook is set
    --save-json         # Save each stage output to ./output/ directory
"""
import os
import sys
import json
import argparse
from datetime import datetime, timezone
from pathlib import Path

# Add project root to path so imports work
sys.path.insert(0, str(Path(__file__).parent))

# Patch DynamoDB for local mode — store in memory instead
_LOCAL_STORE: dict = {}

def _local_save(date_str, sort_key, payload):
    _LOCAL_STORE[f"{date_str}#{sort_key}"] = payload
    print(f"  [LocalDB] Stored {sort_key} for {date_str} ({len(str(payload))} chars)")

def _local_load(date_str, sort_key):
    return _LOCAL_STORE.get(f"{date_str}#{sort_key}")

# Monkeypatch shared.dynamo before any lambda imports it
import shared.dynamo as _dynamo_mod
_dynamo_mod.save_report = _local_save
_dynamo_mod.load_report = _local_load

# Now safe to import handlers
from lambdas.fetch_market_data.handler  import handler as fetch_handler
from lambdas.compute_signals.handler    import handler as signals_handler
from lambdas.compose_newsletter.handler import handler as compose_handler
from lambdas.publish_discord.handler    import handler as discord_handler


def run(date_str: str, skip_news: bool, skip_discord: bool, save_json: bool):
    print("=" * 70)
    print(f"  Market Pulse — Local Run — {date_str}")
    print("=" * 70)

    output_dir = Path("./output")
    if save_json:
        output_dir.mkdir(exist_ok=True)

    # ── Step 1: Fetch market data ────────────────────────────────────────────
    print("\n[STEP 1] Fetching market data...")
    event = {"date": date_str}
    result1 = fetch_handler(event, None)
    print(f"  → {result1}")

    if save_json:
        from shared.config import SK_RAW_DATA
        raw = _LOCAL_STORE.get(f"{date_str}#{SK_RAW_DATA}", {})
        (output_dir / f"{date_str}_raw_data.json").write_text(
            json.dumps(raw, indent=2, default=str)
        )
        print(f"  → Saved to output/{date_str}_raw_data.json")

    # ── Step 2: Compute signals ──────────────────────────────────────────────
    print("\n[STEP 2] Computing signals...")
    result2 = signals_handler({"date": date_str}, None)
    print(f"  → {result2}")

    if save_json:
        from shared.config import SK_SIGNALS
        sig = _LOCAL_STORE.get(f"{date_str}#{SK_SIGNALS}", {})
        (output_dir / f"{date_str}_signals.json").write_text(
            json.dumps(sig, indent=2, default=str)
        )
        print(f"  → Saved to output/{date_str}_signals.json")

    # ── Step 3: Compose newsletter ───────────────────────────────────────────
    print("\n[STEP 3] Composing newsletter with Claude...")
    result3 = compose_handler({"date": date_str}, None)
    print(f"  → Preview: {result3.get('preview', '')}")

    # Print full newsletter to console
    from shared.config import SK_NEWSLETTER
    newsletter_record = _LOCAL_STORE.get(f"{date_str}#{SK_NEWSLETTER}", {})
    newsletter_text   = newsletter_record.get("newsletter", "")

    if save_json:
        (output_dir / f"{date_str}_newsletter.json").write_text(
            json.dumps(newsletter_record, indent=2, default=str)
        )
        (output_dir / f"{date_str}_newsletter.md").write_text(newsletter_text)
        print(f"  → Saved to output/{date_str}_newsletter.md")

    print("\n" + "─" * 70)
    print("NEWSLETTER OUTPUT:")
    print("─" * 70)
    print(newsletter_text)
    print("─" * 70)

    # ── Step 4: Publish to Discord ───────────────────────────────────────────
    if skip_discord:
        print("\n[STEP 4] Skipping Discord publish (--skip-discord)")
    elif not os.environ.get("DISCORD_WEBHOOK_URL"):
        print("\n[STEP 4] Skipping Discord publish (DISCORD_WEBHOOK_URL not set)")
    else:
        print("\n[STEP 4] Publishing to Discord...")
        result4 = discord_handler({"date": date_str}, None)
        print(f"  → {result4}")

    print("\n✅ Done!\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Market Pulse local runner")
    parser.add_argument("--date",         default=None,  help="Date override YYYY-MM-DD")
    parser.add_argument("--skip-news",    action="store_true", help="Use dummy news data")
    parser.add_argument("--skip-discord", action="store_true", help="Skip Discord post")
    parser.add_argument("--save-json",    action="store_true", help="Save outputs to ./output/")
    args = parser.parse_args()

    date_str = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    run(date_str, args.skip_news, args.skip_discord, args.save_json)
