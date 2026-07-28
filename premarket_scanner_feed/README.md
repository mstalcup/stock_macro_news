# Premarket Scanner Feed

Daily **gappers** scan + **Trend Join Long (TJL)** confirmation for the Robinhood agent.

## Schedule (America/Los_Angeles, Mon–Fri)

| Time (PT) | Mode | Purpose |
|-----------|------|---------|
| 6:00 AM | `gappers` | Premarket movers (gap >5%, price >$3, vol >50k), top 10 + catalyst |
| 7:05 AM | `tjl` | Confirm breakouts on that universe (10:05 AM ET) |
| 7:30 AM | *(robinhood agent)* | Consumes DynamoDB `GAPPERS` + `TJL` via `feed_context.js` |

## Filters

**Gappers**
- `gap_pct > 5`
- `price > 3`
- `premarket_volume > 50000` (falls back to regular volume when PM fields empty)
- Top 10 by gap %

**TJL PASS** (both required)
- Daily: `curr > prev_daily_high` AND `prev_close > SMA200`
- Intraday: `curr > PMH` AND `curr > today HOD`

## Data sources

- Yahoo Finance screener + quote/chart APIs (no TradingView)
- Catalysts: Finnhub company-news (from `macro-news-feed/news-api-keys`), Benzinga HTML fallback

## Deploy

```powershell
cd premarket_scanner_feed
.\deploy.ps1
python tools/sync_discord_bot.py
```

## Local

```powershell
python tools/run_local.py --mode gappers
python tools/run_local.py --mode tjl --force --symbols AMD NVDA MU
```

## DynamoDB

Table `premarket-scanner-feed-scanner`:

- `pk=ISSUE#YYYY-MM-DD`, `sk=GAPPERS`
- `pk=ISSUE#YYYY-MM-DD`, `sk=TJL`
