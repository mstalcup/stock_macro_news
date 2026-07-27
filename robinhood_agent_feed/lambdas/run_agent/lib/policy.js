import { listPositions } from "./rh_mcp.js";

/**
 * @param {ReturnType<import("./config.js").loadConfig>} cfg
 */
export function buildPolicy(cfg) {
  return {
    maxOpenPositions: cfg.maxOpenPositions,
    minCashReservePct: cfg.minCashReservePct,
    maxOrdersPerRun: cfg.maxOrdersPerRun,
    maxSingleOrderPct: cfg.maxSingleOrderPct,
    executeTrades: cfg.executeTrades,
    ordersThisRun: 0,
    closesThisRun: 0,
    opensThisRun: 0,
  };
}

/**
 * @param {any[]} positions
 */
export function openPositionCount(positions) {
  const arr = listPositions(positions);
  return arr.filter((p) => {
    const qty = parseFloat(p.quantity || p.shares || p.qty || 0);
    return qty > 0;
  }).length;
}

/**
 * @param {ReturnType<typeof buildPolicy>} policy
 * @param {{ side: string, notional_usd: number, equity_value: number, is_close?: boolean }} order
 */
export function validateOrder(policy, order) {
  if (policy.ordersThisRun >= policy.maxOrdersPerRun) {
    return { ok: false, reason: `max orders per run (${policy.maxOrdersPerRun})` };
  }
  const maxNotional = (order.equity_value * policy.maxSingleOrderPct) / 100;
  if (order.notional_usd > maxNotional && !order.is_close) {
    return {
      ok: false,
      reason: `order $${order.notional_usd.toFixed(0)} exceeds ${policy.maxSingleOrderPct}% cap ($${maxNotional.toFixed(0)})`,
    };
  }
  if (order.side === "buy" && !order.is_close) {
    if (policy.opensThisRun > 0 && policy.closesThisRun === 0 && order.open_count >= policy.maxOpenPositions) {
      return {
        ok: false,
        reason: `at max positions (${policy.maxOpenPositions}) — close before open`,
      };
    }
  }
  return { ok: true };
}

export function recordOrder(policy, { side, is_close }) {
  policy.ordersThisRun += 1;
  if (is_close || side === "sell") policy.closesThisRun += 1;
  if (side === "buy" && !is_close) policy.opensThisRun += 1;
}
