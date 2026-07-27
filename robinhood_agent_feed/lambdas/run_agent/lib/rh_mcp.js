import { Client } from "@modelcontextprotocol/sdk/client/index.js";
import { StreamableHTTPClientTransport } from "@modelcontextprotocol/sdk/client/streamableHttp.js";

/**
 * @param {string} mcpUrl
 * @param {{ token: () => Promise<string|undefined>, onUnauthorized?: () => Promise<void> }} authProvider
 */
export async function connectMcp(mcpUrl, authProvider) {
  const transport = new StreamableHTTPClientTransport(new URL(mcpUrl), {
    authProvider,
  });
  const client = new Client({ name: "robinhood-agent-feed", version: "0.1.0" });
  await client.connect(transport);
  return {
    client,
    async close() {
      try {
        await transport.terminateSession?.();
      } catch {
        /* ignore */
      }
      await client.close();
    },
    async callTool(name, args = {}) {
      const result = await client.callTool({ name, arguments: args });
      return result;
    },
    async listTools() {
      return client.listTools();
    },
  };
}

function parseToolContent(result) {
  if (!result) return null;
  const parts = result.content || [];
  for (const p of parts) {
    if (p.type === "text" && p.text) {
      try {
        return JSON.parse(p.text);
      } catch {
        return p.text;
      }
    }
  }
  return result;
}

export async function mcpGetAccounts(mcp) {
  return parseToolContent(await mcp.callTool("get_accounts", {}));
}

export async function mcpGetPortfolio(mcp, accountNumber) {
  return parseToolContent(
    await mcp.callTool("get_portfolio", { account_number: accountNumber })
  );
}

export async function mcpGetPositions(mcp, accountNumber) {
  return parseToolContent(
    await mcp.callTool("get_equity_positions", { account_number: accountNumber })
  );
}

export async function mcpGetQuotes(mcp, symbols) {
  const syms = Array.isArray(symbols) ? symbols.slice(0, 20) : [symbols];
  return parseToolContent(
    await mcp.callTool("get_equity_quotes", { symbols: syms })
  );
}

export async function mcpReviewOrder(mcp, order) {
  return parseToolContent(await mcp.callTool("review_equity_order", order));
}

export async function mcpPlaceOrder(mcp, order) {
  return parseToolContent(await mcp.callTool("place_equity_order", order));
}

/**
 * Normalize get_accounts payload to an array of account objects.
 * @param {any} accounts
 */
export function listAccounts(accounts) {
  if (!accounts) return [];
  if (Array.isArray(accounts)) return accounts;
  const nested =
    accounts?.data?.accounts ||
    accounts?.accounts ||
    accounts?.results ||
    accounts?.data ||
    [];
  return Array.isArray(nested) ? nested : [];
}

/**
 * Pick the agentic trading account from get_accounts response.
 * @param {any} accounts
 * @param {string} [forcedAccountNumber]
 */
export function findAgenticAccount(accounts, forcedAccountNumber = "") {
  const arr = listAccounts(accounts);
  if (forcedAccountNumber) {
    const hit = arr.find(
      (a) =>
        String(a.account_number || a.rhs_account_number || "") === forcedAccountNumber
    );
    if (hit) return hit;
  }
  const agentic = arr.filter((a) => a.agentic_allowed === true);
  if (agentic.length === 1) return agentic[0];
  if (agentic.length > 1) {
    return agentic.find((a) => (a.nickname || "").toLowerCase().includes("agent")) || agentic[0];
  }
  for (const a of arr) {
    const type = (a.brokerage_account_type || a.type || a.account_type || "").toLowerCase();
    const name = (a.nickname || a.name || "").toLowerCase();
    if (type.includes("agentic") || name.includes("agentic") || name === "agentic") {
      return a;
    }
  }
  if (arr.length === 1) return arr[0];
  return null;
}

export function accountNumber(acct) {
  return (
    acct?.account_number ||
    acct?.rhs_account_number ||
    acct?.id ||
    acct?.account_id ||
    ""
  );
}

/**
 * Policy-only: parse Robinhood MCP portfolio wrapper for order-size caps.
 * The agent sees raw MCP JSON; this avoids duplicating field names in prompts.
 * @param {any} portfolio
 */
export function parsePortfolioSnapshot(portfolio) {
  const row =
    portfolio?.data && typeof portfolio.data === "object" && !Array.isArray(portfolio.data)
      ? portfolio.data
      : portfolio || {};
  const bpField = row.buying_power;
  const buyingPower = parseFloat(
    (typeof bpField === "object" && bpField ? bpField.buying_power : bpField) || 0
  );
  const cash = parseFloat(row.cash || row.cash_available || 0);
  const totalValue = parseFloat(row.total_value || row.totalValue || 0);
  const equityValue = parseFloat(
    row.equity_value || row.equity || row.market_value || row.total_equity || 0
  );
  const spendable = buyingPower || cash;
  const accountValue = totalValue || (equityValue + cash) || spendable;
  return {
    cash,
    buyingPower,
    equityValue,
    totalValue,
    spendable,
    accountValue,
  };
}

/**
 * @param {any} positions
 */
export function listPositions(positions) {
  if (!positions) return [];
  if (Array.isArray(positions)) return positions;
  if (Array.isArray(positions.results)) return positions.results;
  const d = positions.data;
  if (Array.isArray(d)) return d;
  if (d && typeof d === "object") {
    if (Array.isArray(d.positions)) return d.positions;
    if (Array.isArray(d.equity_positions)) return d.equity_positions;
  }
  return [];
}
