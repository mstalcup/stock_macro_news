# Influencer Feed (Current System + Roadmap)

This document reflects the current working state of the `influencer_feed` stack, what has been built, and what is left.

## What Is Built Right Now

### Core data flow

1. **Discovery + fetch pipeline (`FindContent`)**
   - Lists enabled sources from Dynamo (`SOURCE#...` rows)
   - Ingests source metadata for an `issue_date` (`FETCH#...` rows)
   - Fetches transcripts in AWS and stores artifacts to S3
   - Optionally starts compose (controlled by `start_compose`)

2. **Compose pipeline (`ComposeIssue`)**
   - Reads FETCH rows
   - Builds per-source summaries (`ISSUE_SOURCE#...`)
   - Builds global daily summary (`ISSUE#...`)
   - Persists issue row
   - Publishes to Discord webhook

### LLM summarization

- Per-source summarization uses **OpenAI cheap model** (`gpt-4o-mini` by default)
- Global merge uses **OpenAI smart model** (`gpt-4o` by default)
- Both calls include retry/backoff for transient HTTP/network failures
- Responses use **structured JSON schema** (`response_format` / strict schema) when the model supports it; the client falls back to unstructured completion if the API rejects the schema
- Per-source shape: `advice` + `tickers[]` with `ticker`, `direction`, `note`
- Global shape: `overall_advice`, `day_over_day_shift`, `ticker_focus[]` with `ticker`, `consensus`, `note`
- `ISSUE_SOURCE` rows include `source_summary_mode` and `source_summary_model`
- `ISSUE` rows include `global_summary_mode` and `global_summary_model`

### Source roster (two feeds)

Sources are keyed under `USER#<user_id>` as `SOURCE#<source_id>`.

- **`default` — main influencer feed** (equities tilt): Maverick of Wall Street, Patrick Boyle, Joseph Carlson, Forward Guidance, Geeks of Finance, ZipTrader, CoreEdgeTrader (`@CoreEdgeTrader`). Paused on this feed only (`enabled: false`): Benjamin Cowen, InvestAnswers, The Compound (`@the-compound-pod`) — still available on the crypto feed where listed.
- **`crypto` — crypto-only influencer feed**: CryptosRUs, Benjamin Cowen, InvestAnswers, Bankless, Coin Bureau, Altcoin Daily.

Roster lists live in `tools/seed_roster.py` as `DEFAULT_FEED_ENABLED`, `DEFAULT_FEED_PAUSED`, and `CRYPTO_FEED_ENABLED` (not related to the separate `macro_news_feed` headline stack).

Re-seed from the repo after edits:

```powershell
py tools/seed_roster.py --profile mastalcup --region us-east-1 --table influencer-feed-influencer-feed
```

Scheduled Lambdas still default to `user_id=default`. To run the crypto feed on a schedule, invoke the same state machines with `"user_id": "crypto"` (and optionally add a second EventBridge rule / duplicate scheduler stack later).

### Transcript artifacts

Transcripts are written to:

- `v1/user/{user_id}/issue/{issue_date}/source/{source_id}/video/{video_id}.json`
- `v1/user/{user_id}/issue/{issue_date}/source/{source_id}/manifest.json`

FETCH rows are updated with:

- `transcript_status`
- `transcript_s3_bucket`
- `transcript_s3_prefix`
- `transcript_manifest_s3_key`
- `transcript_fetched_at`

### Schedules and automation

- **Hourly EventBridge schedule** calls `influencer-feed-start-find-content-schedule`
- Scheduler Lambda computes current date in `America/Los_Angeles` unless overridden
- Scheduler starts `FindContent` with payload:
  - `issue_date`
  - `user_id`
  - `start_compose` (defaults to `false` for hourly runs)
- **Twice-daily EventBridge schedules** now call `influencer-feed-start-compose-window-schedule`
  - Morning window runner (`slot=morning`)
  - Afternoon window runner (`slot=afternoon`)
  - Each run passes `window_start_utc` + `window_end_utc` into `ComposeIssue`
  - Checkpoint item `META#LAST_COMPOSE_WINDOW` tracks last successful `window_end_utc`

### Discord publishing

- `ComposeIssue` calls `publish_discord` after persist
- Prefer **bot mode** (Secrets Manager `discord-bot` with `bot_token` + `channel_id`): posts digest in-channel, opens a **thread** (`Source notes — <issue_date>` for macro, `Crypto source notes — <issue_date>` for `user_id=crypto`), and posts per-source notes in the thread
- **Webhook** remains as fallback if bot credentials are missing or fail
- Returns `discord_publish_status` in state machine output (`posted_thread`, `posted_webhook`, etc.)

## Current State Machine Behavior

### `FindContent`

`ListSources -> MapIngestSources -> MapFetchTranscripts -> FinalizeAndStartCompose`

- Transcript fetch map currently has `MaxConcurrency: 3`
- `FinalizeAndStartCompose` starts compose only when `start_compose=true`

**Fetch sanity check:** For a given `issue_date`, if every source is `NO_NEW`, transcript fetch is skipped (nothing new to pull). If you need to confirm AWS transcript fetch, look for `FETCH#<date>#<source>` with `status=FETCHED`, `transcript_status=FETCHED`, and `transcript_fetched_at` set (e.g. after a day with new videos).

### `ComposeIssue`

`LoadFetchRows -> MapSummarizeSources -> BuildGlobalSummary -> PersistIssue -> PublishDiscord`

## Secrets In Use

- `OpenAiApiKeySecretArn`
  - `{"api_key":"sk-..."}`
- `DiscordWebhookSecretArn`
  - `{"webhook_url":"https://discord.com/api/webhooks/..."}`
  - Optional multi-feed: `{"webhook_url":"...","feeds":{"crypto":{"webhook_url":"..."}}}`
- `DiscordBotSecretArn`
  - Single feed: `{"bot_token":"<token>","channel_id":"<id>"}`
  - **Multi-feed (macro vs crypto):** `feeds.<user_id>` overrides top-level. Example — one bot, two channels; or two bots:
    ```json
    {
      "bot_token": "<macro-or-shared-bot>",
      "channel_id": "<macro-channel-id>",
      "feeds": {
        "crypto": {
          "bot_token": "<optional-separate-crypto-bot>",
          "channel_id": "<crypto-channel-id>"
        }
      }
    }
    ```
  - `publish_discord` picks credentials using `user_id` from the compose event (`default` → macro, `crypto` → crypto). Missing `feeds.crypto` falls back to top-level token/channel (same destination as macro).

### Production note: schedules vs feeds

- Hourly / compose schedulers today default to **`user_id=default`** only.
- Running the **crypto** pipeline is the same Step Functions + Lambdas with **`user_id":"crypto"`**; give it its own EventBridge rules when you want a different cadence (e.g. crypto compose 2× daily at different hours).
- Discord routing does **not** require a second AWS secret if you use the `feeds` object above; use a second secret only if you prefer isolating credentials per environment.
- `WebshareProxySecretArn`
  - `{"proxy_url":"http://user:pass@host:port"}`
  - or `{"proxy_urls":["http://u:p@h1:port","http://u:p@h2:port", ...]}`

## Proxy Sync (Important)

Use local proxy list as source of truth and sync it into AWS secret:

```powershell
py tools/sync_webshare_secret.py `
  --proxy-file tools/proxies.txt `
  --secret-arn "<WebshareProxySecretArn>" `
  --profile mastalcup `
  --region us-east-1
```

This script supports:

- `http://user:pass@host:port`
- `host:port:user:pass`

and writes normalized `proxy_urls` JSON to Secrets Manager.

## Manual Test Commands

### Trigger hourly starter manually (with override date)

```powershell
python -c "import boto3,json; lam=boto3.Session(profile_name='mastalcup', region_name='us-east-1').client('lambda'); r=lam.invoke(FunctionName='influencer-feed-start-find-content-schedule', InvocationType='RequestResponse', Payload=json.dumps({'issue_date':'2026-05-05','user_id':'default','start_compose':False}).encode('utf-8')); print(r['Payload'].read().decode())"
```

### Compose only (manual)

```powershell
aws stepfunctions start-execution `
  --profile mastalcup `
  --region us-east-1 `
  --state-machine-arn <ComposeIssueStateMachineArn> `
  --input "{\"issue_date\":\"2026-05-05\",\"user_id\":\"default\"}"
```

### Trigger windowed compose scheduler manually

```powershell
python -c "import boto3,json; lam=boto3.Session(profile_name='mastalcup', region_name='us-east-1').client('lambda'); r=lam.invoke(FunctionName='influencer-feed-start-compose-window-schedule', InvocationType='RequestResponse', Payload=json.dumps({'slot':'morning','user_id':'default'}).encode('utf-8')); print(r['Payload'].read().decode())"
```

## Known Limitations / Notes

- Even with Webshare, YouTube may still intermittently block some requests (`RequestBlocked` / `IpBlocked`).
- Reliability improves substantially with larger proxy pools and lower parallel pressure.
- Current prompts are functional but not yet high quality for end-reader usefulness.
- Current source mix still needs curation.

## Next Steps (Planned)

### 1) Prompt quality overhaul

Rework prompts and output contracts for:

- stronger signal extraction (macro stance, changes vs prior, actionable trade context)
- tighter format consistency
- better global synthesis (agreement/disagreement, regime shifts, risk framing)

### 2) Influencer roster refocus

- prune low-signal channels
- prioritize channels with consistent macro/trade edge
- potentially tier sources (core vs secondary) in summarization

### 3) Macro news feed (started)

See **`../macro_news_feed/`** — Alpha Vantage + NewsAPI + Finnhub → S3 (`deduped.json` per slot). Scheduled pre-open / pre-close PT. Wire into compose + Discord later.

### 4) Reliability hardening

- optional reduction of transcript fetch concurrency
- per-source success metrics + alarms
- periodic proxy sync automation
- pin/correct schedule behavior for DST transitions (current cron expressions are UTC-based)

## Deploy

```powershell
cd influencer_feed
.\deploy.ps1
```

`deploy.ps1` installs required vendored deps and runs `sam build` + `sam deploy --no-confirm-changeset`.
