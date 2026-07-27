# Macro News Feed

Scheduled macro headline collection for pre-market open and pre-close (Pacific). Artifacts land in S3 for a future summarizer (same idea as `influencer_feed` transcripts).

## Architecture

```
EventBridge (6:25am / 12:50pm PT, Mon–Fri)
    └─► start-fetch-schedule Lambda
            └─► Step Functions: macro-news-feed-fetch-news
                    ├─► fetch-news-slot Lambda → S3 deduped.json
                    └─► compose-news-slot Lambda → S3 digest.json
                    ├─► publish-discord-macro (pre_open by default)
                    └─► start-llm-panel → llm-sentiment-feed query-panel (pre_open)
```

Debug runs in **Step Functions → Executions**. Input: `{"issue_date":"2026-05-15","slot":"pre_open"}`.

## Sources (active vs optional)

| Provider | Status | Role |
|----------|--------|------|
| **NewsAPI** | **On** | US `top-headlines` (business + general) — closest to “what’s on the front page” |
| **Finnhub** | **On** | Market wires: `general`, `forex`, `crypto`, `merger` |
| **Alpha Vantage** | **Off** (code kept) | `NEWS_SENTIMENT` is ticker-centric (valuation notes, single symbols). Good later for per-ticker sentiment, not macro topics. |

Toggle in `lambdas/fetch_news_slot/newslib/config.py` → `ENABLED_PROVIDERS`.

### Why not Seeking Alpha / Yahoo?

| Source | Reality |
|--------|---------|
| **Seeking Alpha** | No official headline API; content is mostly stock-specific opinion. Scraping is brittle and ToS-risky. |
| **Yahoo Finance** | No supported public news API (`yfinance` is prices, not a macro wire). RSS exists but unofficial. |

### Future adds for “topics driving sentiment”

- **Marketaux** — broad financial news + entity sentiment (free tier is tight: 3 articles/request).
- **Polygon / Benzinga** — strong market news, usually paid.
- **LLM summarize step** (next) — cluster NewsAPI+Finnhub headlines into themes (Iran, oil, Fed, BTC), which is the real goal.

Up to **10 articles per source**, merged into **`deduped.json`** (URL match + title fallback). Overlaps get a `providers[]` list on each article.

## Schedule (Pacific target, Mon–Fri)

| Slot | Target (PT) | Purpose |
|------|-------------|---------|
| `pre_open` | **6:25 AM** | Before US cash open |
| `pre_close` | **12:50 PM** | Before US cash close |

EventBridge rules use **UTC** cron (`13:25` and `19:50` UTC = PDT). During **PST**, bump to `14:25` / `20:50` UTC in `template.yaml`. `issue_date` is always computed in `America/Los_Angeles` inside the Lambda.

## Ranking philosophy

We want **headline-tier** stories (Iran, oil, Fed, BTC if it’s dominating), not niche ticker pieces.

- **NewsAPI** — US `top-headlines` (business + general), filtered to the issue day.
- **Finnhub** — `general`, `forex`, `crypto`, `merger` wires; newest in-window items.
- **Alpha Vantage** — disabled for macro digest; see table above.

## Issue date = Pacific calendar day

`issue_date=2026-05-15` means articles whose **published time** falls on **May 15 in America/Los_Angeles** (midnight–midnight), not “whatever was fetched today.” The S3 path uses the same date. Re-run a slot after deploy if an older fetch mixed in next-day headlines.

### Per-source S3 cache

If `by_source/{provider}.json` already exists for that `issue_date` + `slot` and has articles, fetch **skips the API** and reuses that file. Deduped + manifest are still rebuilt. Bypass with `force_refresh: true` on the state machine input or `py tools/backfill_slots.py --force-refresh`.

## S3 layout

```
s3://{bucket}/v1/date=2026-05-15/slot=pre_open/
  manifest.json
  by_source/alpha_vantage.json
  by_source/newsapi.json
  by_source/finnhub.json
  deduped.json
  digest.json
```

`manifest.json` points at all keys and dedupe stats. **`deduped.json`** is the fetch output; **`digest.json`** is the trading digest (LLM or heuristic).

### Digest output (`digest.json`)

After fetch, **compose-news-slot** runs automatically:

1. **Article notes** (gpt-4o-mini): macro theme + market impact per headline  
2. **Macro digest** (gpt-4o): executive summary, dominant themes, catalysts, asset-class notes, ticker watch, risks, actionable takeaways, vs prior slot  

Compares to the **prior digest** when available (same-day `pre_open` before `pre_close`, else prior day same slot).

`digest_markdown` is formatted for a future Discord post. Set OpenAI key:

```powershell
py tools/sync_openai_secret.py --profile mastalcup
```

## Setup

### 1. API keys

- Alpha Vantage: https://www.alphavantage.co/support/#api-key  
- NewsAPI: https://newsapi.org/register  
- Finnhub: https://finnhub.io/register (free tier)

### 2. Local test

```powershell
cd macro_news_feed
# copy keys into ..\..\.env or macro_news_feed\.env
py tools/run_fetch_local.py --slot pre_open
```

### 3. Deploy

```powershell
cd macro_news_feed
$env:AWS_PROFILE = "mastalcup"
.\deploy.ps1
```

### 4. Store secrets in AWS

After deploy, push keys from `.env`:

```powershell
py tools/sync_news_secrets.py --profile mastalcup --stack macro-news-feed
```

### 5. Manual invoke (AWS)

```powershell
aws lambda invoke --profile mastalcup --function-name macro-news-feed-fetch-news-slot `
  --payload '{"slot":"pre_open"}' out.json
Get-Content out.json
```

## Discord

Posts to channel **1505454996567625768** after compose (default: **`pre_open` only**).

```powershell
py tools/sync_discord_bot.py --profile mastalcup   # copies bot_token from influencer-feed
```

Template parameter `DiscordPublishSlots`: set to `pre_open,pre_close` when ready for twice-daily posts.

### Afternoon recap (pre_close) — time-filtered news

Yes, with caveats:

| Source | Since-morning filter |
|--------|----------------------|
| **Finnhub** | Returns latest wires; we filter by `published_at` in code. **Works** with `fetch_window` (6:25 AM PT → midnight). |
| **NewsAPI `/everything`** | Supports `from` / `to` UTC. **Works** for afternoon window. |
| **NewsAPI top-headlines** | No time filter on API — still useful but not “since 6:25” precise. |

`pre_close` fetch uses **6:25 AM PT → end of day**; compose prompt emphasizes **changes since morning**. Enable Discord for both slots via `DiscordPublishSlots=pre_open,pre_close`.

## Operator tools

| Script | Purpose |
|--------|---------|
| `py tools/setup_check.py` | Keys in env + AWS secret + stack outputs |
| `py tools/setup_check.py --smoke-fetch` | Local HTTP test per provider |
| `py tools/sync_news_secrets.py` | Push `.env` keys to Secrets Manager (merges partial) |
| `py tools/invoke_fetch.py --slot pre_open` | Start FetchNews state machine |
| `py tools/backfill_slots.py --dates 2026-05-15 --slots pre_open,pre_close` | Backfill |
| `py tools/print_slot.py --latest --slot pre_open` | Print headlines from S3 |
| `py tools/run_compose_local.py --latest --slot pre_open` | Compose digest locally (needs OpenAI in `.env`) |
| `py tools/print_digest.py --latest --slot pre_open` | Print composed digest |
| `py tools/sync_openai_secret.py` | Push `OPENAI_API_KEY` to Secrets Manager |

Latest pointer per slot: `s3://…/v1/latest/slot=pre_open.json` → `deduped_s3_key`.

## When you’re back (5 min checklist)

1. Create `macro_news_feed/.env` or repo-root `.env` with `ALPHA_VANTAGE_API_KEY`, `NEWS_API_KEY`, `FINNHUB_API_KEY`, `OPENAI_API_KEY`.
2. `py tools/sync_news_secrets.py --profile mastalcup`
3. `py tools/sync_openai_secret.py --profile mastalcup`
4. `sam build && sam deploy` (from `macro_news_feed`)
5. `py tools/setup_check.py --smoke-fetch`
6. `py tools/invoke_fetch.py --slot pre_open` (fetch + compose)
7. `py tools/print_digest.py --latest --slot pre_open`

## Next steps

- [x] Discord publish (`publish-discord-macro`, channel 1505454996567625768)
- [ ] Enable `pre_close` Discord when afternoon recap is tuned
- [ ] **`llm_sentiment_feed/`** — 4-model daily picks + 30d scoring (see `../llm_sentiment_feed/README.md`)
