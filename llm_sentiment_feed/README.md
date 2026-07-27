# LLM Sentiment Feed

Daily panel of **OpenAI**, **Gemini**, and **Grok** (Perplexity optional later) for 30-calendar-day US equity picks, grounded in the macro `digest.json` (hybrid context).

## Locked rules

| Topic | Choice |
|-------|--------|
| Context | Hybrid — macro digest + deduped headlines |
| Schedule | Auto-start after **macro-news-feed** `pre_open` Step Functions completes |
| Entry | **Same-day close** (scored live after that day ends) |
| Exits | **T+7** and **T+30** calendar days (live only, no backfill) |
| Prices | **Yahoo Finance** chart API (primary); Finnhub `/stock/candle` optional fallback (paid tier only — free Finnhub keys work for macro **news**, not candles) |
| Storage | DynamoDB `llm-sentiment-feed-sentiment` + S3 artifacts |

## Architecture

```
Step Functions `llm-sentiment-feed-run-sentiment` (like macro `fetch-news`):
  QueryLlmPanel → ScoreRecommendations → end

Triggered by macro-news-feed after `pre_open` compose (`start-llm-panel`).

EventBridge ~1:05 PM PT — extra score pass for T+7 / T+30 as dates become eligible
```

## Setup

```powershell
cd llm_sentiment_feed
# Add PERPLEXITY_API_KEY, GEMINI_API_KEY, GROK_API_KEY to .env (OpenAI/Finnhub copied from macro)
py tools/sync_secrets.py --profile mastalcup
.\deploy.ps1
```

Requires macro digest at `s3://{macro-bucket}/v1/date={date}/slot=pre_open/digest.json`.

## Test run (prefix `test/`)

```powershell
# Deploy with test prefix
sam deploy --parameter-overrides "MacroArtifactsBucket=macro-news-feed-newsartifactsbucket-qfoqryswgxiv TestRun=true"

py tools/run_panel_local.py --issue-date 2026-05-15 --force-refresh
py tools/print_picks.py --issue-date 2026-05-15 --test

# Optional: fake same-day entry via latest quote (not official close)
py tools/invoke_score.py --issue-date 2026-05-15 --allow-test-quote

# Cleanup
py tools/delete_test_run.py --issue-date 2026-05-15

# Back to production prefix
sam deploy --parameter-overrides "MacroArtifactsBucket=macro-news-feed-newsartifactsbucket-qfoqryswgxiv TestRun=false"
```

## Scoring fidelity

The scorer **never** backfills historical picks. It only updates rows when:

- `issue_date < today` → set **entry** from that day's daily close
- `issue_date + 7 <= today` → set **T+7** exit
- `issue_date + 30 <= today` → set **T+30** exit

Run live on schedule for meaningful performance data.

### Price / scoring debug

```powershell
py tools/debug_prices.py --symbol NVDA --trade-date 2026-05-15
py tools/invoke_score.py
py tools/print_performance.py
```
