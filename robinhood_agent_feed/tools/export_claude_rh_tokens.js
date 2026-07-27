#!/usr/bin/env node
/**
 * Copy Robinhood Trading MCP tokens from Claude Code → tokens.json
 *
 * After you authenticate Robinhood in Claude Code (browser flow), tokens live in:
 *   %USERPROFILE%\.claude\.credentials.json  (Windows)
 *   ~/.claude/.credentials.json              (macOS/Linux)
 *
 * Usage:
 *   node tools/export_claude_rh_tokens.js
 *   node tools/bootstrap_rh_auth.js
 */
import { readFileSync, writeFileSync } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = dirname(fileURLToPath(import.meta.url));
const root = join(__dirname, "..");

function findRhEntry(mcpOAuth) {
  if (!mcpOAuth || typeof mcpOAuth !== "object") return null;
  for (const entry of Object.values(mcpOAuth)) {
    const url = (entry.serverUrl || "").toLowerCase();
    const name = (entry.serverName || "").toLowerCase();
    if (url.includes("agent.robinhood.com") || name.includes("robinhood")) {
      return entry;
    }
  }
  return null;
}

function main() {
  const credPath = join(homedir(), ".claude", ".credentials.json");
  let raw;
  try {
    raw = readFileSync(credPath, "utf8");
  } catch {
    console.error(`Could not read ${credPath}`);
    console.error("Authenticate Robinhood in Claude Code first (/mcp → robinhood-trading).");
    process.exit(1);
  }

  const creds = JSON.parse(raw);
  const entry = findRhEntry(creds.mcpOAuth);
  if (!entry?.accessToken) {
    console.error("No robinhood-trading accessToken in mcpOAuth.");
    console.error("In Claude Code: /mcp → authenticate robinhood-trading via browser.");
    process.exit(1);
  }

  const expires_at =
    entry.expiresAt && Number.isFinite(entry.expiresAt)
      ? new Date(entry.expiresAt).toISOString()
      : "";

  const out = {
    access_token: entry.accessToken,
    refresh_token: entry.refreshToken || "",
    device_token: "",
    expires_at,
    user_uuid: "",
    client_id: entry.clientId || "",
    note: "Claude/Cursor MCP: refresh_token + client_id is enough; device_token optional",
  };

  const dest = join(root, "tokens.json");
  writeFileSync(dest, JSON.stringify(out, null, 2) + "\n", "utf8");
  console.log(`Wrote ${dest}`);
  console.log(`  access_token: ${out.access_token.length} chars`);
  console.log(`  refresh_token: ${out.refresh_token ? out.refresh_token.length + " chars" : "(missing)"}`);
  console.log(`  expires_at: ${expires_at || "unknown"}`);
  console.log(`  client_id: ${out.client_id || "(missing)"}`);
  if (!out.refresh_token) {
    console.log("");
    console.log("refresh_token missing — re-auth Robinhood in Claude (/mcp) then re-run this export.");
  } else {
    console.log("");
    console.log("device_token not needed for Claude MCP refresh (refresh_token + client_id is enough).");
  }
  console.log("");
  console.log("Next: py tools/bootstrap_rh_auth.py");
}

main();
