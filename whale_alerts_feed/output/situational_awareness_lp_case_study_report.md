# Fund case study: Situational Awareness LP

CIKs: 2038540, 2045724
Schedule filings: 2 | Unique tickers: 2

## Hypothesis buckets

- **13g_first**: 1 tickers
- **13d_first**: 1 tickers
- **volume_early**: 1 tickers
- **8k_before_schedule**: 0 tickers
- **schedule_only**: 0 tickers

## Positions (backward scan)

### CORZZ
- First filing: `13d_increase` on 2025-10-14
- Volume spike: none in lookback
- Issuer 8-K: none

### NBIS
- First filing: `13g_new` on 2026-05-27
- Volume spike: 2026-05-13 (2.75x median, 14d before filing)
- Issuer 8-K: none
- **Earliest detectable:** 2026-05-13 via `volume_spike`

## Alpha comparison

| Track | n@5d | med α5d | n@20d | med α20d |
|-------|------|---------|-------|----------|
| Earliest detectable | 1 | -7.3112% | 0 | —% |
| First schedule filing | 1 | 20.2814% | 0 | —% |

## Takeaway

- 1 ticker(s) entered via **13G first** (passive lane) — watch `13g_new` / `13g_increase`, not only 13D.
- Volume spikes preceded schedule filing on some names — viable early layer.

## 13F book (puts / longs — where chip shorts appear)

- 13D/G only fires when a stake crosses 5% — most SA chip exposure is 13F puts/options and sub-5% equity; schedule layer misses the book.
- Latest 13F (2026-05-18): 42 lines, $102.69B total, $8.46B puts (8% of book)

**Top chip puts (latest quarter):**
- VANECK ETF TRUST: $2043M notional
- NVDA: $1568M notional
- ORCL-PD: $1073M notional
- AVGO: $1006M notional
- AMD: $969M notional
- MU: $584M notional
- TSMWF: $535M notional
- ASML HLDG NV N Y REG: $494M notional
- INTC: $159M notional
- GLW: $21M notional

**QoQ put stack change** (2026-02-11 → 2026-05-18): $+8.45B
- New puts: VANECK ETF TRUST ($2043M), NVIDIA CORPORATION ($1568M), ORACLE CORP ($1073M), BROADCOM INC ($1006M), ADVANCED MICRO DEV ($969M), MICRON TECHNOLOGY  ($584M), TAIWAN SEMICONDUCT ($535M), ASML HLDG NV N Y R ($494M)