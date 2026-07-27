# Sector rotation feed (v1)

Daily post to Discord **#sector-rotation** (`1509077723245445211`):

- 11 GICS Select Sector SPDRs vs **SPY** (relative 1d / 5d / 20d heatmap)
- **IN / OUT** rotation scoreboard (RS vs SPY above/below 20d MA + momentum)
- Drill-down on top **2 IN** sectors: top 5d movers + high relative volume among approximate top holdings

## Deploy

```powershell
cd sector_rotation_feed
py tools/sync_discord_bot.py   # once: copy bot token from influencer-feed
.\deploy.ps1
```

## Local preview

```powershell
cd sector_rotation_feed
py tools/run_local.py
```

## Schedule

Weekdays **6:30 AM Pacific** (`America/Los_Angeles`) — at US cash open (9:30 AM ET). DST handled via EventBridge ScheduleV2.

## Holdings

`lambdas/run_rotation/rotationlib/holdings_data.json` is a static approximate top-12 per sector (S&P 500 constituents). Refresh periodically from fund factsheets.
