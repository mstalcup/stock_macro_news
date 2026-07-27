import { loadConfig } from "./lib/config.js";
import { newRunId, writeRunEnd, writeRunStart, recordTradeEvent, loadPositionLedger } from "./lib/audit.js";
import { runAgentLoop } from "./lib/agent_loop.js";
import { formatDigest, loadDiscordConfig, postDiscordMessage } from "./lib/discord.js";
import { buildMarketContext } from "./lib/news.js";
import {
  createAuthProvider,
  getValidTokens,
  loadAgentKeys,
} from "./lib/rh_auth.js";
import {
  accountNumber,
  connectMcp,
  findAgenticAccount,
  mcpGetAccounts,
  mcpGetPortfolio,
  mcpGetPositions,
  listPositions,
} from "./lib/rh_mcp.js";
import { buildPolicy } from "./lib/policy.js";

/**
 * @param {Record<string, unknown>} event
 */
export async function handler(event = {}) {
  const cfg = loadConfig();
  const runId = newRunId();
  const readOnly = event.read_only === true || event.readOnly === true;

  const digest = {
    run_id: runId,
    thesis: "",
    hold: [],
    close: [],
    open: [],
    executed: [],
    notes: "",
    error: "",
  };

  if (!cfg.agentEnabled && !readOnly && !event.force) {
    digest.notes = "AGENT_ENABLED=false — skipping run.";
    await maybeDiscord(cfg, digest);
    return { statusCode: 200, body: JSON.stringify({ skipped: true, run_id: runId }) };
  }

  let mcp = null;
  try {
    await writeRunStart(cfg.tableName, runId, {
      read_only: readOnly,
      agent_enabled: cfg.agentEnabled,
      execute_trades: cfg.executeTrades && !readOnly,
    });

    const tokens = await getValidTokens(cfg.rhOAuthSecretArn);
    const authProvider = createAuthProvider(tokens, cfg.rhOAuthSecretArn);

    mcp = await connectMcp(cfg.mcpUrl, authProvider);

    const accounts = await mcpGetAccounts(mcp);
    const acct = findAgenticAccount(accounts, cfg.agentAccountNumber);
    if (!acct) {
      throw new Error("No agentic account found — create and fund Agentic Trading in Robinhood desktop");
    }
    const acctNum = accountNumber(acct);
    if (!acctNum) {
      throw new Error("Could not resolve agentic account_number from get_accounts");
    }

    const portfolio = await mcpGetPortfolio(mcp, acctNum);
    const positions = await mcpGetPositions(mcp, acctNum);

    if (readOnly) {
      const summary = {
        run_id: runId,
        account_number: acctNum,
        portfolio,
        position_count: listPositions(positions).length,
        read_only: true,
      };
      await writeRunEnd(cfg.tableName, runId, summary);
      return { statusCode: 200, body: JSON.stringify(summary) };
    }

    const keys = await loadAgentKeys(cfg.agentKeysSecretArn);

    const policy = buildPolicy({
      ...cfg,
      executeTrades: cfg.executeTrades,
    });

    const posList = positions?.results || positions?.data || positions || [];
    const newsBlock = await buildMarketContext(cfg);
    const heldSymbols = listPositions(positions).map((p) =>
      String(p.symbol || p.ticker || "").toUpperCase()
    );
    const positionLedger = await loadPositionLedger(cfg.tableName, heldSymbols);

    const agentResult = await runAgentLoop({
      cfg,
      policy,
      mcp,
      accountNumber: acctNum,
      portfolio,
      positions,
      newsBlock,
      apiKey: keys.anthropic_api_key,
      positionLedger,
    });

    digest.thesis = agentResult.finish.thesis || "";
    digest.hold = agentResult.finish.hold || [];
    digest.close = agentResult.finish.close || [];
    digest.open = agentResult.finish.open || [];
    digest.notes = agentResult.finish.notes || "";
    digest.executed = agentResult.executed;

    for (const trade of agentResult.tradeEvents || []) {
      if (trade.status !== "submitted") continue;
      const row = { ...trade };
      if (row.action === "open" && !row.thesis_id && row.symbol) {
        row.thesis_id = `${runId}-${row.symbol}`;
        row.parent_thesis_id = row.thesis_id;
        row.root_thesis_id = row.thesis_id;
      }
      if (row.action !== "open" && row.thesis_id) {
        row.parent_thesis_id = row.parent_thesis_id || row.thesis_id;
        row.root_thesis_id = row.thesis_id;
      }
      await recordTradeEvent(cfg.tableName, runId, row);
    }

    await writeRunEnd(cfg.tableName, runId, {
      thesis: digest.thesis,
      hold: digest.hold,
      close: digest.close,
      open: digest.open,
      executed: digest.executed,
      trade_events: agentResult.tradeEvents,
      reviews: agentResult.reviews,
    });

    await maybeDiscord(cfg, digest).catch((err) => {
      console.error("Discord digest failed:", err);
    });

    return {
      statusCode: 200,
      body: JSON.stringify({
        run_id: runId,
        thesis: digest.thesis,
        executed: digest.executed,
      }),
    };
  } catch (err) {
    const msg = err instanceof Error ? err.message : String(err);
    digest.error = msg;
    console.error(err);
    await writeRunEnd(cfg.tableName, runId, { error: msg }).catch(() => {});
    if (!readOnly) {
      await maybeDiscord(cfg, digest).catch(() => {});
    }
    return { statusCode: 500, body: JSON.stringify({ run_id: runId, error: msg }) };
  } finally {
    if (mcp) await mcp.close();
  }
}

async function maybeDiscord(cfg, digest) {
  if (!cfg.discordSecretArn) return;
  const { token, channelId: secretChannel } = await loadDiscordConfig(cfg.discordSecretArn);
  const channelId = cfg.discordChannelId || secretChannel;
  const content = formatDigest(digest);
  await postDiscordMessage(token, channelId, content);
}
