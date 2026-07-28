import Anthropic from "@anthropic-ai/sdk";
import {
  mcpGetQuotes,
  mcpPlaceOrder,
  mcpReviewOrder,
  listPositions,
  parsePortfolioSnapshot,
} from "./rh_mcp.js";
import {
  openPositionCount,
  recordOrder,
  validateOrder,
} from "./policy.js";

const SYSTEM_PROMPT = `You are an autonomous equity portfolio manager for a Robinhood Agentic (sandbox) account. You have full discretion to decide what to buy, sell, or hold. Bias toward action over inertia when risk/reward is clear.

## How you trade
- Full auto: when you are convinced a trade is warranted, execute it — no manual confirmation.
- Use review_equity_order then place_equity_order when EXECUTE_TRADES is on.
- Use get_equity_quotes and the context provided to inform decisions; you may also reason from general market knowledge when helpful.

## Position review (do this FIRST every run)
For each held name, compute unrealized P&L vs average cost / ledger entry_level and decide hold / trim / close:
1. **Target hit or +10% unrealized** — strongly prefer reduce or close unless a fresh accelerating catalyst justifies letting it run. Locking gains is good portfolio management, not "giving up."
2. **+5% to +10%** — tighten thesis: raise stop toward breakeven/entry, or trim if cash is tight / better setups exist.
3. **Stop hit or thesis broken** — close; do not hope.
4. **Dust / sub-$1 residuals** — note as non-executable; do not invent a hold thesis for them.
5. **Cash below MIN_CASH_RESERVE_PCT** — do not freeze. Free capital by trimming large winners or weak theses before skipping the whole book. Selling winners to rebalance is preferred over "hold everything because cash is low."

Example: a name up ~12% with no new catalyst (e.g. LMT after a defense bid fades) should be a trim/close candidate, not a default hold.

## Before opening a new position (action "open")
Work through multiple lenses and only open if you can articulate a coherent case on each:
1. **Technical** — price action, trend, momentum, key levels, risk/reward from the chart/setup.
2. **Fundamental** — business quality, valuation, catalysts, why this name vs alternatives.
3. **News / macro** — what is driving the trade now (headlines, sector flows, events); cite specific reasoning, not vague sentiment.
4. **Strategy** — a clear plan: entry_level, stop_level, exit_target, and what would invalidate the thesis.

Prefer **premarket scanner TJL PASS** names (confirmed daily + intraday breakout) over unconfirmed gappers or vague sector ideas. Gappers without TJL PASS are watchlist only unless confluence is strong.

If you cannot develop a concrete strategy with entry and exit/stop levels, **do not open** — explain in finish_run why you passed.

For **add**, **reduce**, or **close** on an existing holding:
- Load the **position strategy ledger** for that symbol (original_thesis + trade_history).
- Compare current price/setup to the original entry_level, stop_level, exit_target.
- If fundamentals changed, stop hit, target hit, large unrealized gain without new catalyst, or thesis invalidated → adjust (reduce/close) and explain vs the original reasoning.
- Always set thesis_id to the **root** thesis id from the ledger; every follow-on trade points back to that same id.

## Trade documentation (every order)
- action: "open" | "add" | "reduce" | "close"
- justification: your multi-lens reasoning (technical + fundamental + news/macro as applicable)
- strategy: short label (e.g. momentum_breakout, mean_reversion, catalyst_play, take_profit)
- opens require entry_level, stop_level, exit_target
- full exits: fully_exited=true

## Book rules (hard limits)
- Max MAX_OPEN_POSITIONS concurrent equity positions; at cap, close before open.
- Respect MIN_CASH_RESERVE_PCT cash buffer for *new buys* — but exits/trims are always allowed and encouraged when they improve the book.
- No shorting, margin, or options in v1.

## Data you receive
- Raw Robinhood MCP JSON for portfolio and positions.
- **Position strategy ledger** per held symbol: original open thesis (reasoning + levels) and chronological trade_history — use this to continue or exit prior strategies.
- Cross-feed intelligence including **premarket scanner** (gappers + TJL hits) — treat TJL PASS as high-priority long candidates.

When done, call finish_run with thesis, hold, close, open lists and notes. Explicitly mention which winners you reviewed for take-profit.`;

const ORDER_FIELDS = {
  symbol: { type: "string" },
  side: { type: "string", enum: ["buy", "sell"] },
  amount: { type: "number", description: "Dollar amount for market order" },
  action: { type: "string", enum: ["open", "add", "reduce", "close"] },
  justification: { type: "string", description: "Multi-lens reasoning: technical, fundamental, news/macro" },
  strategy: { type: "string" },
  entry_level: { type: "string", description: "Target entry price or zone (required on open)" },
  stop_level: { type: "string", description: "Stop-loss price or rule (required on open)" },
  exit_target: { type: "string", description: "Take-profit / exit target (required on open)" },
  thesis_id: {
    type: "string",
    description: "Root thesis id from position ledger (required for add/reduce/close; omit on open)",
  },
  fully_exited: {
    type: "boolean",
    description: "true when this sell fully closes the position/thesis",
  },
  is_close: { type: "boolean" },
};

/**
 * @param {object} ctx
 */
export async function runAgentLoop(ctx) {
  const {
    cfg,
    policy,
    mcp,
    accountNumber,
    portfolio,
    positions,
    newsBlock,
    apiKey,
    positionLedger = [],
  } = ctx;
  const client = new Anthropic({ apiKey });

  const snap = parsePortfolioSnapshot(portfolio);
  const accountValue = snap.accountValue;
  const openCount = openPositionCount(positions);

  const userContext = [
    `Run policy: max_positions=${policy.maxOpenPositions}, min_cash_reserve_pct=${policy.minCashReservePct},`,
    `max_orders=${policy.maxOrdersPerRun}, max_single_order_pct=${policy.maxSingleOrderPct},`,
    `execute_trades=${policy.executeTrades}`,
    "",
    `Account number: ${accountNumber}`,
    "(Policy caps use parsed account value for order-size limits only.)",
    "",
    jsonBlock("Portfolio (raw MCP get_portfolio)", portfolio),
    jsonBlock("Positions (raw MCP get_equity_positions)", positions),
    "",
    positionLedger.length
      ? jsonBlock("Position strategy ledger (original thesis + trade history per symbol)", positionLedger)
      : "## Position strategy ledger\n(none — no held positions with tracked theses yet)",
    "",
    newsBlock,
    "",
    policy.executeTrades
      ? "EXECUTE_TRADES is ON — execute after review when your judgment says go."
      : "EXECUTE_TRADES is OFF — review only; place_equity_order will be skipped.",
  ].join("\n");

  const tools = [
    {
      name: "get_equity_quotes",
      description: "Fetch latest quotes for up to 20 symbols",
      input_schema: {
        type: "object",
        properties: { symbols: { type: "array", items: { type: "string" } } },
        required: ["symbols"],
      },
    },
    {
      name: "review_equity_order",
      description: "Preflight an equity order via Robinhood MCP",
      input_schema: {
        type: "object",
        properties: ORDER_FIELDS,
        required: ["symbol", "side", "amount", "action", "justification", "strategy"],
      },
    },
    {
      name: "place_equity_order",
      description: "Place equity order after successful review",
      input_schema: {
        type: "object",
        properties: ORDER_FIELDS,
        required: ["symbol", "side", "amount", "action", "justification", "strategy"],
      },
    },
    {
      name: "finish_run",
      description: "Complete the run with thesis and summary lists",
      input_schema: {
        type: "object",
        properties: {
          thesis: { type: "string" },
          hold: { type: "array", items: { type: "string" } },
          close: { type: "array", items: { type: "string" } },
          open: { type: "array", items: { type: "string" } },
          notes: { type: "string" },
        },
        required: ["thesis", "hold", "close", "open"],
      },
    },
  ];

  const messages = [{ role: "user", content: userContext }];
  const executed = [];
  const tradeEvents = [];
  const reviews = [];
  let finish = null;
  const maxIter = 18;

  for (let i = 0; i < maxIter; i++) {
    const resp = await client.messages.create({
      model: cfg.anthropicModel,
      max_tokens: 4096,
      system: SYSTEM_PROMPT.replaceAll("MAX_OPEN_POSITIONS", String(policy.maxOpenPositions))
        .replaceAll("MIN_CASH_RESERVE_PCT", String(policy.minCashReservePct)),
      tools,
      messages,
    });

    const toolUses = resp.content.filter((b) => b.type === "tool_use");
    const textBlocks = resp.content.filter((b) => b.type === "text");

    if (resp.stop_reason === "end_turn" && !toolUses.length) {
      finish = {
        thesis: textBlocks.map((t) => t.text).join("\n") || "Hold — no changes.",
        hold: [],
        close: [],
        open: [],
        notes: "",
      };
      break;
    }

    if (!toolUses.length) break;

    messages.push({ role: "assistant", content: resp.content });
    const toolResults = [];

    for (const tu of toolUses) {
      const input = tu.input || {};
      let result;

      try {
        if (tu.name === "get_equity_quotes") {
          result = await mcpGetQuotes(mcp, input.symbols || []);
        } else if (tu.name === "review_equity_order") {
          const tradeErr = validateTradeInput(input);
          if (tradeErr) {
            result = { error: tradeErr };
          } else {
            const check = validateOrder(policy, {
              side: input.side,
              notional_usd: input.amount,
              equity_value: accountValue,
              is_close: input.action === "close" || input.is_close,
              open_count: openCount,
            });
            if (!check.ok) {
              result = { error: check.reason };
            } else {
              const order = buildMcpOrder(accountNumber, input);
              const review = await mcpReviewOrder(mcp, order);
              reviews.push({ ...input, review });
              tradeEvents.push(tradeEventFromInput(input, { review, status: "reviewed" }));
              result = review;
            }
          }
        } else if (tu.name === "place_equity_order") {
          const tradeErr = validateTradeInput(input);
          if (tradeErr) {
            result = { error: tradeErr };
          } else if (!policy.executeTrades) {
            tradeEvents.push(tradeEventFromInput(input, { status: "skipped", reason: "EXECUTE_TRADES=false" }));
            result = { skipped: true, reason: "EXECUTE_TRADES=false" };
          } else {
            const check = validateOrder(policy, {
              side: input.side,
              notional_usd: input.amount,
              equity_value: accountValue,
              is_close: input.action === "close" || input.is_close,
              open_count: openCount,
            });
            if (!check.ok) {
              result = { error: check.reason };
            } else {
              const order = buildMcpOrder(accountNumber, input);
              const placed = await mcpPlaceOrder(mcp, order);
              recordOrder(policy, {
                side: input.side,
                is_close: input.action === "close" || input.is_close,
              });
              const label = formatTradeLabel(input);
              executed.push(label);
              tradeEvents.push(tradeEventFromInput(input, { placed, status: "submitted" }));
              result = placed;
            }
          }
        } else if (tu.name === "finish_run") {
          finish = input;
          result = { ok: true };
        } else {
          result = { error: `unknown tool ${tu.name}` };
        }
      } catch (err) {
        result = { error: err instanceof Error ? err.message : String(err) };
      }

      toolResults.push({
        type: "tool_result",
        tool_use_id: tu.id,
        content: JSON.stringify(result),
      });
    }

    messages.push({ role: "user", content: toolResults });
    if (finish) break;
  }

  if (!finish) {
    finish = {
      thesis: "Run ended without explicit finish_run.",
      hold: [],
      close: [],
      open: [],
      notes: "",
    };
  }

  return {
    finish,
    executed,
    tradeEvents,
    reviews,
    messages_summary: messages.length,
  };
}

function jsonBlock(label, obj) {
  const s = JSON.stringify(obj, null, 2);
  const max = 12000;
  return `## ${label}\n${s.length > max ? `${s.slice(0, max)}\n...(truncated)` : s}`;
}

function validateTradeInput(input) {
  const action = input.action;
  if (!action) return "action is required (open|add|reduce|close)";
  if (action === "open") {
    if (!input.entry_level || !input.stop_level || !input.exit_target) {
      return "open requires entry_level, stop_level, and exit_target (your strategy plan)";
    }
    if (!(input.justification || "").trim()) {
      return "open requires justification covering your analysis (technical, fundamental, news/macro)";
    }
  } else if (!input.thesis_id) {
    return `${action} requires thesis_id referencing the original open thesis`;
  }
  return null;
}

function tradeEventFromInput(input, extra = {}) {
  const symbol = String(input.symbol || "").toUpperCase();
  const action = String(input.action || "open");
  const rootThesis = input.thesis_id ? String(input.thesis_id) : undefined;
  return {
    symbol,
    side: input.side,
    amount: input.amount,
    action,
    justification: input.justification || "",
    strategy: input.strategy || "",
    entry_level: input.entry_level ?? null,
    stop_level: input.stop_level ?? null,
    exit_target: input.exit_target ?? null,
    thesis_id: rootThesis || (action === "open" ? undefined : ""),
    parent_thesis_id: rootThesis,
    root_thesis_id: rootThesis,
    fully_exited: Boolean(input.fully_exited),
    is_close: input.action === "close" || input.is_close,
    timestamp: new Date().toISOString(),
    ...extra,
  };
}

function formatTradeLabel(input) {
  return `${input.action?.toUpperCase()} ${input.side?.toUpperCase()} ${input.symbol} $${input.amount} — ${input.justification}`;
}

function buildMcpOrder(accountNumber, input) {
  // Robinhood MCP: additionalProperties=false — only documented fields allowed.
  return {
    account_number: accountNumber,
    symbol: String(input.symbol || "").toUpperCase(),
    side: input.side,
    type: "market",
    dollar_amount: Number(input.amount || 0).toFixed(2),
    time_in_force: "gfd",
    market_hours: "regular_hours",
  };
}
