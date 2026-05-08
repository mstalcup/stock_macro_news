# Market Pulse — Daily Market Digest System

Automated morning newsletter covering macro news, sector rotation signals, and market momentum.
Runs on AWS (EventBridge → Step Functions → Lambda) and stores data in DynamoDB, then posts to Discord.

## Architecture

```
EventBridge (8:30am ET, Mon–Fri)
    └─► Step Functions
            ├─[1] fetch_market_data   → DynamoDB (raw)
            ├─[2] compute_signals     → DynamoDB (signals)
            ├─[3] compose_newsletter  → DynamoDB (newsletter)
            └─[4] publish_discord     → Discord webhook
```

## DynamoDB Schema

Table: `market-pulse`
- PK: `date` (YYYY-MM-DD)
- SK: `report_type` (raw_data | signals | newsletter)
- TTL: 90 days

## Data Sources

| Source | What | Cost |
|--------|------|------|
| yfinance | ETF prices, 1d/5d/20d history | Free |
| Alpha Vantage | News sentiment feed w/ ticker tags | Free tier (25 req/day) |
| NewsAPI | Macro headlines (top 20) | Free tier (100 req/day) |
| Anthropic Claude | Newsletter synthesis | ~$0.01/day |

## Setup

### 1. Install dependencies (local dev)
```bash
pip install -r requirements.txt
```

### 2. Environment variables
```bash
# Required
ALPHA_VANTAGE_API_KEY=your_key
NEWS_API_KEY=your_key
ANTHROPIC_API_KEY=your_key
DISCORD_WEBHOOK_URL=your_webhook_url
DYNAMODB_TABLE=market-pulse
AWS_REGION=us-east-1

# Optional (for local testing without AWS)
LOCAL_MODE=true
```

### 3. Local test run
```bash
python run_local.py
```

### 4. Deploy to AWS
```bash
cd infrastructure
./deploy.sh
```

## Sector ETFs Tracked

- XLK (Tech), XLF (Financials), XLE (Energy)
- XLV (Health), XLI (Industrials), XLY (Consumer Discretionary)
- XLP (Consumer Staples), XLB (Materials), XLC (Comms)
- XLRE (Real Estate), XLU (Utilities)
- GLD (Gold), IBIT (Bitcoin), TLT (Bonds), USO (Oil), UUP (Dollar)

## Rotation Signal Logic

Each sector gets a momentum score: `(1d_return × 0.5) + (5d_return × 0.3) + (20d_return × 0.2)`

Rotation alerts fire when:
- A sector's score jumps > 1.5 std deviations above its 20-day average
- Two+ related sectors move together (macro-driven cluster)
- A sector's short-term momentum crosses above its long-term trend
