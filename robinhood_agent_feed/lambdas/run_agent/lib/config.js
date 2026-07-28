/** @typedef {{ access_token: string, refresh_token: string, device_token: string, expires_at?: string, user_uuid?: string, client_id?: string }} RhTokens */

const RH_TOKEN_URL = "https://api.robinhood.com/oauth2/token/";
/**
 * Prefer `client_id` from the secret (Claude: `…-claude`, Cursor: different).
 * Web/app id `c82SH0WZ…` is rejected by agent.robinhood.com MCP ("client id not allowed").
 * Leave empty so refresh fails loudly if secret omits client_id.
 */
const RH_CLIENT_ID = "";

export function loadConfig() {
  return {
    tableName: (process.env.AGENT_TABLE_NAME || "").trim(),
    rhOAuthSecretArn: (process.env.RH_OAUTH_SECRET_ARN || "").trim(),
    agentKeysSecretArn: (process.env.AGENT_KEYS_SECRET_ARN || "").trim(),
    discordSecretArn: (process.env.DISCORD_BOT_SECRET_ARN || "").trim(),
    discordChannelId: (process.env.AGENT_DISCORD_CHANNEL_ID || "").trim(),
    agentAccountNumber: (process.env.RH_AGENT_ACCOUNT_NUMBER || "").trim(),
    agentEnabled: (process.env.AGENT_ENABLED || "false").toLowerCase() === "true",
    executeTrades: (process.env.EXECUTE_TRADES || "true").toLowerCase() === "true",
    maxOpenPositions: parseInt(process.env.MAX_OPEN_POSITIONS || "8", 10),
    minCashReservePct: parseFloat(process.env.MIN_CASH_RESERVE_PCT || "5"),
    maxOrdersPerRun: parseInt(process.env.MAX_ORDERS_PER_RUN || "12", 10),
    maxSingleOrderPct: parseFloat(process.env.MAX_SINGLE_ORDER_PCT || "35"),
    anthropicModel: (process.env.ANTHROPIC_MODEL || "claude-sonnet-4-6").trim(),
    macroBucket: (process.env.MACRO_ARTIFACTS_BUCKET || "").trim(),
    contextSlot: (process.env.CONTEXT_SLOT || "pre_open").trim(),
    contextIssueDate: (process.env.CONTEXT_ISSUE_DATE || "").trim(),
    llmSentimentTable: (process.env.LLM_SENTIMENT_TABLE || "llm-sentiment-feed-sentiment").trim(),
    influencerTable: (process.env.INFLUENCER_TABLE || "influencer-feed-influencer-feed").trim(),
    influencerUserId: (process.env.INFLUENCER_USER_ID || "default").trim(),
    rotationTable: (process.env.ROTATION_TABLE || "sector-rotation-feed-rotation").trim(),
    scannerTable: (process.env.SCANNER_TABLE || "premarket-scanner-feed-scanner").trim(),
    marketPulseTable: (process.env.MARKET_PULSE_TABLE || "").trim(),
    mcpUrl: (process.env.RH_MCP_URL || "https://agent.robinhood.com/mcp/trading").trim(),
  };
}

export { RH_TOKEN_URL, RH_CLIENT_ID };
