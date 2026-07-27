#!/usr/bin/env node
/**
 * Local smoke test — read-only MCP against agentic account.
 * Requires tokens in Secrets Manager (bootstrap_rh_auth.js) and deployed stack secrets.
 *
 * Usage:
 *   node tools/invoke_local.js --read-only
 *   node tools/invoke_local.js --run          # full agent (respects AGENT_ENABLED locally via env)
 */
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");

const envPath = join(root, ".env");
try {
  for (const line of readFileSync(envPath, "utf8").splitlines()) {
    const t = line.trim();
    if (!t || t.startsWith("#") || !t.includes("=")) continue;
    const i = t.indexOf("=");
    const k = t.slice(0, i).trim();
    const v = t.slice(i + 1).trim();
    process.env[k] = v;
  }
} catch {
  /* no .env */
}

function parseArgs() {
  const args = new Set(process.argv.slice(2));
  return {
    readOnly: args.has("--read-only"),
    run: args.has("--run"),
    force: args.has("--force"),
  };
}

async function main() {
  const opts = parseArgs();
  if (!opts.readOnly && !opts.run) {
    console.log("Pass --read-only or --run");
    process.exit(1);
  }

  // Point handler at stack secrets when running locally
  const stack = process.env.STACK_NAME || "robinhood-agent-feed";
  const region = process.env.AWS_REGION || "us-east-1";
  process.env.AWS_PROFILE = process.env.AWS_PROFILE || "mastalcup";

  process.env.AGENT_TABLE_NAME = process.env.AGENT_TABLE_NAME || `${stack}-agent`;
  process.env.RH_OAUTH_SECRET_ARN = process.env.RH_OAUTH_SECRET_ARN || `${stack}/rh-oauth`;
  process.env.AGENT_KEYS_SECRET_ARN = process.env.AGENT_KEYS_SECRET_ARN || `${stack}/agent-keys`;
  process.env.DISCORD_BOT_SECRET_ARN = process.env.DISCORD_BOT_SECRET_ARN || `${stack}/discord-bot`;

  process.env.AGENT_ENABLED = opts.force ? "true" : process.env.AGENT_ENABLED || "true";
  process.env.EXECUTE_TRADES = process.env.EXECUTE_TRADES || "false";
  process.env.RH_MCP_URL = process.env.RH_MCP_URL || "https://agent.robinhood.com/mcp/trading";

  const { handler } = await import("../lambdas/run_agent/handler.js");
  const event = opts.readOnly
    ? { read_only: true }
    : { force: opts.force || false };

  const result = await handler(event);
  console.log(JSON.stringify(result, null, 2));
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
