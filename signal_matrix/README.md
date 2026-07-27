# Signal matrix

Cross-feed **recommendation overlap** for macro news, LLM sentiment panel, influencer digest, and future channels (hedge-fund holdings, etc.).

## Quick start

```powershell
cd signal_matrix
py tools/print_matrix.py --issue-date 2026-05-20
py tools/print_matrix.py --issue-date 2026-05-20 --markdown
py tools/print_matrix.py --list-providers
```

Requires AWS profile `mastalcup` (or `--profile`) and deployed stacks: `macro-news-feed`, `llm-sentiment-feed`, `influencer-feed`.

## Providers (plugins)

| `provider_id` | Channel | Source |
|---------------|---------|--------|
| `macro` | `macro` | S3 `digest.json` → `ticker_watchlist` |
| `llm_sentiment` | `llm_sentiment` | DynamoDB panel picks (per model column) |
| `influencer` | `influencer` | DynamoDB `global_ticker_focus` + per-source `source_tickers` |
| `hedge_fund` | `hedge_fund` | Optional local seed JSON (stub for 13F-style holdings) |

Default run: `macro,llm_sentiment,influencer` (no hedge until you pass a seed).

### Hedge fund holdings (preview)

Holdings use `kind=holding` — shown in the matrix but **do not** count as trade votes for confluence tiers (so a static long 13F line does not fake “unanimous” with today’s short call).

```powershell
py tools/print_matrix.py --issue-date 2026-05-20 --providers macro,llm_sentiment,influencer,hedge_fund `
  --hedge-seed seed/hedge_holdings.example.json
```

## Confluence tiers

Per ticker, trade signals are rolled up **per channel** (one vote per feed family), then:

| Tier | Meaning |
|------|---------|
| `unanimous` | 3+ channels agree (all long or all short) |
| `strong` | 3+ channels with a majority side |
| `lean` | 2 channels agree |
| `solo` | 1 channel only |
| `split` | Channels disagree (long vs short) |

## Adding a new channel

1. Create `signal_matrix/providers/your_feed.py` subclassing `SignalProvider`.
2. Emit `SignalVote` rows with a unique `source_id` and stable `channel_id`.
3. Register in `signal_matrix/registry.py`:

```python
from .providers.your_feed import YourFeedProvider

PROVIDER_REGISTRY["your_feed"] = YourFeedProvider
```

4. Run: `py tools/print_matrix.py --providers macro,llm_sentiment,influencer,your_feed`

Use `kind="holding"` for slow-moving positions (hedge funds); `kind="trade"` for directional daily calls.

## Tests

```powershell
cd signal_matrix
py -m pytest tests/ -q
```

## Discord (LLM sentiment channel)

After each panel run, `llm-sentiment-feed-publish-discord-sentiment` posts:

1. Daily panel picks (existing)
2. **Signal confluence** — high-alignment tickers across macro + LLM + influencer
3. **LLM panel tracking** — per-model MTM / T+7 when available

## Roadmap

- Persist `SIGNAL#{issue_date}` to DynamoDB / S3
- Join LLM `return_7d` / `return_30d` to score which tiers predict best
- Real 13F / hedge fund ingest pipeline replacing the seed file
