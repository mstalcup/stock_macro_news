# Robinhood Agentic portfolio agent

Daily unattended Lambda that reads your **Robinhood Agentic** sandbox account via the official Trading MCP, plans rebalances with Anthropic, executes trades (optional), audits to DynamoDB, and posts a Discord digest.

## Prerequisites (you did step 1)

1. **Robinhood Agentic account** created and funded (desktop app).
2. AWS profile `mastalcup` (or set `AWS_PROFILE`).
3. `.env` with `ANTHROPIC_API_KEY` (and optional `FINNHUB_API_KEY`).
4. Discord channel for daily digest (reuse bot from `influencer-feed`).

## Quick start

```powershell
cd robinhood_agent_feed
copy .env.example .env          # fill ANTHROPIC_API_KEY
.\deploy.ps1                    # npm install + sam build + deploy
py tools/sync_agent_secrets.py  # Anthropic/Finnhub → agent-keys secret
py tools/sync_discord_bot.py --channel-id YOUR_CHANNEL_ID
```

### OAuth bootstrap (one-time)

Robinhood tokens **never** go in `.env`. You need `access_token`, `refresh_token`, and **`device_token`** (required for Lambda auto-refresh).

**Full step-by-step:** [docs/OAUTH_BOOTSTRAP.md](docs/OAUTH_BOOTSTRAP.md)

Short version:

1. Re-authenticate Robinhood MCP in Cursor; capture `device_token` from the OAuth **request** and tokens from the **response** (DevTools → Network → `oauth2/token`).
2. Put them in `tokens.json` (see `tokens.json.example`).
3. `py tools/bootstrap_rh_auth.py` → `py tools/check_rh_secret.py` → `node tools/invoke_local.js --read-only`

**Future:** phone “Bot Keeper” to upload tokens — [docs/MOBILE_BOT_KEEPER_PLAN.md](docs/MOBILE_BOT_KEEPER_PLAN.md).

### Smoke test

```powershell
node tools/invoke_local.js --read-only
```

Expect JSON with `account_number`, `portfolio`, and `position_count`.

Dry-run full agent (no trades — `EXECUTE_TRADES` defaults false locally):

```powershell
$env:EXECUTE_TRADES="false"
node tools/invoke_local.js --run --force
```

### Go live

1. In AWS Console → Lambda `robinhood-agent-feed-run-agent` → Environment:
   - `AGENT_ENABLED` = `true`
   - `EXECUTE_TRADES` = `false` for one planning-only day, then `true`
2. Schedule: weekdays **7:30 AM Pacific** (EventBridge).

Kill switch: set `AGENT_ENABLED=false` anytime.

## Secrets shape

| Secret | JSON keys |
|--------|-----------|
| `robinhood-agent-feed/rh-oauth` | `access_token`, `refresh_token`, `device_token`, `expires_at`, `user_uuid` |
| `robinhood-agent-feed/agent-keys` | `anthropic_api_key`, `finnhub_api_key` |
| `robinhood-agent-feed/discord-bot` | `bot_token`, `channel_id` |

Lambda refreshes `access_token` automatically (~8.5 day lifetime). Re-run bootstrap only if refresh fails for days (password change, revoked session).

## Policy defaults

| Env | Default | Meaning |
|-----|---------|---------|
| `MAX_OPEN_POSITIONS` | 6 | Concentrated book |
| `MIN_CASH_RESERVE_PCT` | 10 | Keep dry powder |
| `MAX_ORDERS_PER_RUN` | 8 | Cap daily churn |
| `MAX_SINGLE_ORDER_PCT` | 25 | Max one trade vs equity |

Close-before-open enforced when at position cap.

## Architecture

```
EventBridge (weekdays 7:30 PT)
  → Lambda run_agent
      → Secrets Manager (OAuth refresh)
      → Robinhood MCP (agent.robinhood.com/mcp/trading)
      → Anthropic tool loop
      → DynamoDB audit (RUN# / TRADE# / POSITION#)
      → Discord digest
```

## Re-auth alert

If you see `auth_expired` or refresh errors in Discord/logs, re-run:

```powershell
py tools/bootstrap_rh_auth.py
```
