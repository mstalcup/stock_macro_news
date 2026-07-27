# Whale alerts feed

Anticipatory institutional signals **before 13F** — 13D/G filings, not 13F headline pumps.

## Phase 0 — backtest & roster (run first)

```powershell
cd whale_alerts_feed
py tools/backfill_edgar.py --years 2
py tools/run_backtest.py --write-report
py tools/print_fund_rankings.py --top 30
```

Outputs:

- `output/fund_rankings.json` — funds ranked on anticipatory alpha + anticipation link rate
- `output/backtest_report.md` — human summary
- `seed/fund_roster.json` — approved static watchlist (edit before deploy)

Manual Tier S overrides: `seed/manual_overrides.json` (e.g. Situational Awareness LP).

**Deploy roster (recommended):** curated hedge funds, not raw backtest auto-rank (filing-agent CIKs need XML parse — Phase 0.5):

```powershell
py tools/build_roster.py --curated-only
```

## Phase 1 — deploy live poll

```powershell
py tools/sync_discord_bot.py --channel-id YOUR_CHANNEL_ID
.\deploy.ps1
py tools/sync_roster.py
```

Lambda `whale-alerts-feed-poll-whales` runs every **30 min** 5 AM–5 PM Pacific weekdays. **Immediate Discord** on new 13D/G with issuer ticker. **No** 13F filing alerts.

## Phase 2 — 8-K, volume, news (multi-layer backtest)

Each layer emits `WhaleSignal` rows → same forward-return scorer as layer 1.

```powershell
# 1) Backfill signal caches (8-K parse + volume/news lookback before known filings)
py tools/backfill_phase2.py --years 2

# Optional: set FINNHUB_API_KEY for news layer (repo .env or macro-news-feed secrets)
# py tools/backfill_phase2.py --skip-news   # if no key

# 2) Score each layer + confluence clusters
py tools/run_phase2_backtest.py --write-report
```

Outputs: `output/phase2_backtest_report.md`, `output/phase2_confluence.json`

**Layers**

| Layer | Source | Backtest idea |
|-------|--------|----------------|
| `schedule_13dg` | Layer 1 (curated scored) | Filing-day baseline |
| `8k_stake` | 8-K text on roster funds | Stake disclosure before/at 13D |
| `volume_spike` | Yahoo volume | Spike in days *before* a 13D/8-K filing |
| `news_stake` | Finnhub company-news | Activist headline before filing |
| `confluence` | 2+ layers, same ticker, 14d window | Combined urgent alert candidate |

## Phase 4 — anonymous options flow (daily + weekly report)

Large options flow monitor — **no fund attribution**. Builds a running ledger from contract volume/OI; weekly summary for Discord.

```powershell
py tools/build_options_watchlist.py          # S&P 500 + ETFs (~500 tickers)
py tools/poll_options_flow.py                # daily (slow: ~3–8 min on Yahoo)
py tools/poll_options_flow.py --limit 50     # smoke test
py tools/options_weekly_report.py --write-report --llm   # needs OPENAI_API_KEY
```

**Data sources (in order):**
- `POLYGON_API_KEY` — snapshot + optional block prints (recommended; no scraping)
- Yahoo options chain fallback — contract-day volume × price (not individual prints)

Do **not** scrape Unusual Whales (ToS, brittle). Outputs: `output/cache/options_flow_events.json`, `options_ledger.json`, `output/options_weekly_report.md`.

## Phase 3 — fund case study & 13F dashboard

```powershell
# Backward scan + 13F puts/long history (default: Situational Awareness LP)
py tools/run_fund_case_study.py --write-report --deep-13f

# Rolled-up 13F portfolio (curated funds)
py tools/build_13f_index.py --write-report

# Static HTML dashboard (embeds JSON — works via file://)
py tools/build_dashboard_data.py --refresh
# Open output/dashboard/index.html in a browser

# SA public-intel scan (chip shorts: essay, news, 13F pivot)
py tools/run_sa_intel_scan.py --write-report
```

Outputs: `output/situational_awareness_lp_case_study_report.md`, `output/whale_13f_index.json`, `output/dashboard/whale_13f_dashboard_data.json`

## Design notes

- **North star:** beat the 13F filing; filing-day pops are bot-crowded (see backtest `median_filing_day_alpha_1d`)
- **13F** in backtest = confirmation track + `anticipation_link_rate` only
- Re-run Phase 0 quarterly to refresh `fund_roster.json`
