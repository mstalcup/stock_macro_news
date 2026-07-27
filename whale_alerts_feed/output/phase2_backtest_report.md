# Phase 2 multi-layer backtest

Generated: 2026-06-07T07:22:37.691325Z

## Per-layer performance (excess vs SPY)

| Layer | n | med a5d | med a20d | hit% @20d |
|-------|---|---------|----------|-----------|
| schedule_13dg | 546 | 0.3313 | -0.8277 | 47.2 |
| 8k_stake | 2 | -4.4966 | -9.8755 | 0 |
| volume_spike | 65 | -2.7961 | 4.9262 | 60.0 |
| news_stake | 5 | 3.1322 | -4.953 | 40.0 |
| confluence | 79 | -1.5514 | 2.5216 | 57.0 |

## Confluence (2+ layers same ticker within window)

- Clusters found: **82**
- Scored confluence anchors: **79**
- Median alpha @20d: **2.5216%**, hit rate: **57.0%**

## How to read this

- **8k_stake**: ownership language in fund 8-K filings.
- **volume_spike**: unusual volume in days before a known 13D/8-K (anticipation test).
- **news_stake**: activist/stake headlines before filing (Finnhub).
- **confluence**: 2+ layers on same ticker — candidate for urgent alerts.