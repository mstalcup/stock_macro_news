# Robinhood OAuth bootstrap (Agentic MCP)

Lambda needs these fields in `robinhood-agent-feed/rh-oauth` for long-lived unattended runs:

| Field | Purpose |
|-------|---------|
| `access_token` | Bearer token for MCP (~8.5 days) |
| `refresh_token` | Renews access token without browser |
| `client_id` | MCP client (e.g. Claude `…-claude`) — **required** for refresh |
| `device_token` | Optional for Claude/Cursor MCP; required only for web/app clients (which MCP rejects anyway) |

**Recommended path:** authenticate Robinhood MCP in Claude Code, then:

```powershell
node tools\export_claude_rh_tokens.js
py tools\bootstrap_rh_auth.py
```

No Network-tab capture and no `robinhood-for-agents` web login.

---

## Quick path (recommended): browser network capture

Use this on **desktop** (Robinhood requires desktop for Agentic MCP OAuth).

### 1. Start a fresh MCP login

In **Cursor**:

1. Open MCP settings and connect/authenticate **Robinhood** (`https://agent.robinhood.com/mcp/trading`).
2. When the browser window opens for Robinhood login, **before** you submit credentials:
   - Open **DevTools** (F12) → **Network** tab.
   - Enable **Preserve log**.
   - Filter: `oauth2/token` or `token`.

### 2. Complete login

Log in with your Robinhood credentials and MFA as usual.

### 3. Capture tokens from the token exchange

Find the **POST** to `https://api.robinhood.com/oauth2/token/` (status 200).

**Request** (Payload / Request body):

- Copy `device_token` (UUID string). If you ever re-bootstrap, reuse the **same** `device_token` for this bot.

**Response** (JSON):

- `access_token`
- `refresh_token`
- `expires_in` (seconds) — optional; used to compute `expires_at`

### 4. Fill `tokens.json`

In `robinhood_agent_feed/tokens.json` (gitignored):

```json
{
  "access_token": "<from response>",
  "refresh_token": "<from response>",
  "device_token": "<from request>",
  "client_id": "<from JWT meta.oid or Claude export>",
  "expires_at": "<ISO8601 or leave empty>",
  "user_uuid": "<from response if present>"
}
```

`client_id` must be an **MCP-allowed** client (Claude: `…-claude`, Cursor: similar). Web/app `c82SH0WZ…` is rejected by the Trading MCP.

`expires_at` example: if `expires_in` is `734000`, set to roughly now + that many seconds in ISO format.

Copy from `tokens.json.example` if the file does not exist.

### 5. Push to AWS and verify

```powershell
cd robinhood_agent_feed
py tools\bootstrap_rh_auth.py
py tools\check_rh_secret.py
```

`check_rh_secret.py` should show `device_token: set`.

```powershell
node tools\invoke_local.js --read-only
```

Expect JSON with `account_number`, `portfolio`, and `position_count`.

Optional — confirm Lambda:

```powershell
aws lambda invoke `
  --function-name robinhood-agent-feed-run-agent `
  --profile mastalcup `
  --region us-east-1 `
  --payload "{\"read_only\":true}" `
  --cli-binary-format raw-in-base64-out `
  out.json `
  --cli-read-timeout 120
Get-Content out.json
```

---

## Alternative: Claude export (access only)

If Claude Code already has Robinhood MCP authenticated:

```powershell
node tools\export_claude_rh_tokens.js
py tools\bootstrap_rh_auth.py
```

This writes `access_token`, `refresh_token`, `client_id` (e.g. `…-claude`), and `expires_at`.
**`device_token` is still empty** — MCP works until `expires_at` (~8 days). For Lambda auto-refresh, capture `device_token` from the **MCP** `oauth2/token` Network tab (same client as Claude/Cursor), not from a normal Robinhood web login.

---

## What does **not** work for MCP

| Method | Problem |
|--------|---------|
| `robinhood-for-agents` / web app browser login | Tokens use client `c82SH0WZ…` → MCP returns `401 client id not allowed` |
| `export_claude_rh_tokens.js` alone | No `device_token` → no auto-refresh after expiry |
| Robinhood mobile app session | Separate client; not Agentic MCP |
| Mixing web `device_token` with Claude `refresh_token` | Device binding mismatch; refresh fails |

---

## What not to rely on for long-lived refresh

| Method | Gets MCP-allowed `device_token`? |
|--------|----------------------|
| `export_claude_rh_tokens.js` alone | No |
| Web / `robinhood-for-agents` login | Wrong client for MCP |
| Robinhood mobile app session | No |

---

## When to re-bootstrap

- CloudWatch: `Robinhood token refresh failed`
- Scheduled agent runs fail at startup in `rh_auth.js`
- You disconnect the agent in Robinhood app or change password
- Roughly every few months if refresh chain is revoked

Re-bootstrap uses the same steps. Prefer **keeping the same `device_token`** unless Robinhood forces a new session.

---

## See also

- [MOBILE_BOT_KEEPER_PLAN.md](./MOBILE_BOT_KEEPER_PLAN.md) — phone “refresh connection” app (future)
