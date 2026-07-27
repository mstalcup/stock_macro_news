# Strategy sweep — curated 13D/G alerts

As-of: 2026-05-26

| Strategy | n | med α5d | hit 5d | med α20d | hit 20d | med α60d |
|----------|---|---------|--------|----------|---------|----------|
| baseline_all | 546 | 0.2857 | 51.4 | -0.8277 | 47.3 | -3.1358 |
| excl_volume_filers | 176 | 0.5919 | 53.5 | -1.1171 | 46.0 | -4.9402 |
| excl_volume_and_berkshire | 158 | 0.7809 | 54.9 | -0.883 | 47.5 | -5.6409 |
| 13d_only | 64 | -1.3422 | 42.4 | -1.5901 | 43.8 | -5.3336 |
| 13d_new_only | 9 | 3.1322 | 55.6 | -1.3205 | 44.4 | -7.0675 |
| 13d_dedupe | 20 | 1.5948 | 57.1 | 1.9856 | 55.0 | -4.5536 |
| 13d_dedupe_excl_noise | 18 | 1.5948 | 57.9 | -0.3168 | 50.0 | -5.1833 |
| 13d_dedupe_activist | 18 | 1.5948 | 57.9 | -0.3168 | 50.0 | -5.1833 |
| 13d_dedupe_greenlight_starboard | 11 | 3.3698 | 75.0 | 0.6869 | 54.5 | -6.9196 |
| 13d_new_activist_dedupe | 9 | 3.1322 | 55.6 | -1.3205 | 44.4 | -7.0675 |
| starboard_13d_new | 6 | 6.1998 | 83.3 | 1.8507 | 50.0 | -3.2857 |
| greenlight_13g | 74 | 3.095 | 71.6 | 4.102 | 56.8 | -0.4532 |
| hybrid_starboard13d_greenlight13g | 78 | 3.5941 | 73.1 | 5.5719 | 57.7 | 0.3456 |

## Recommendation

**Deploy:** `hybrid_starboard13d_greenlight13g` — med α20d 5.5719% over 78 signals (in-sample).

Fund-specific hybrid (Starboard 13d_new + Greenlight 13g) is implemented in `whalelib/alert_policy.py`.
Exclude Millennium/Point72 13G; default activist funds get 13D-only alerts.