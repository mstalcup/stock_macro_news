# Offline: capture RH tokens via robinhood-for-agents

Inspected clone: `C:\Users\masta\workspace\robinhood-for-agents` @ `38e5b91`.

**Do not run full `onboard`** — it installs MCP into Claude/Codex. Use **browser login only**.

## Security review (summary)

- Playwright opens Chrome to `robinhood.com/login`; you type credentials; tool only intercepts `/oauth2/token`.
- Tokens saved locally (keychain or encrypted file). No upload/telemetry found to non-Robinhood hosts.
- Prefer local clone over `npx`. Prefer `browserLogin` over `onboard`.

## Offline commands (PowerShell)

```powershell
# 0. Bun required: https://bun.sh  (install while online if needed)
#    Chrome required.

# 1. Use the inspected clone (already at workspace\robinhood-for-agents)
cd C:\Users\masta\workspace\robinhood-for-agents
git checkout 38e5b91
bun install

# 2. Explicit file store (Windows-friendly)
$exportDir = "$env:USERPROFILE\.robinhood-export"
New-Item -ItemType Directory -Force -Path $exportDir | Out-Null
$env:ROBINHOOD_TOKENS_FILE = "$exportDir\tokens.enc"
$bytes = New-Object byte[] 32
[System.Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
$env:ROBINHOOD_TOKEN_KEY = [Convert]::ToBase64String($bytes)
# optional: save key so dump works in a new shell
$env:ROBINHOOD_TOKEN_KEY | Set-Content -Encoding ascii "$exportDir\token.key"

# 3. Browser login ONLY (Chrome opens — log in + MFA yourself)
bun -e "import { browserLogin } from './src/server/browser-auth.ts'; const r = await browserLogin(); console.log(r)"

# 4. Dump tokens to JSON (same shell / same env vars!)
bun -e "import { loadTokens } from './src/client/token-store.ts'; const t = await loadTokens(); if (!t) throw new Error('No tokens'); if (!t.device_token) throw new Error('Missing device_token'); console.log(JSON.stringify({ access_token: t.access_token, refresh_token: t.refresh_token, device_token: t.device_token, expires_at: '' }, null, 2))" | Out-File -Encoding utf8 "$exportDir\rh-tokens.json"

# 5. Confirm device_token present WITHOUT printing secrets
py -c "import json; d=json.load(open(r'$env:USERPROFILE\.robinhood-export\rh-tokens.json',encoding='utf-8')); print('device_token:', 'set' if d.get('device_token') else 'MISSING'); print('access:', len(d.get('access_token') or ''), 'refresh:', len(d.get('refresh_token') or ''))"
```

## After you're back online (with Cursor)

```powershell
# Copy into agent feed (gitignored)
Copy-Item "$env:USERPROFILE\.robinhood-export\rh-tokens.json" C:\Users\masta\workspace\stock_macro_news\robinhood_agent_feed\tokens.json

cd C:\Users\masta\workspace\stock_macro_news\robinhood_agent_feed
py tools\bootstrap_rh_auth.py
py tools\check_rh_secret.py
node tools\invoke_local.js --read-only
```

## Cleanup (after bootstrap succeeds)

```powershell
Remove-Item -Recurse -Force "$env:USERPROFILE\.robinhood-export" -ErrorAction SilentlyContinue
Remove-Item C:\Users\masta\workspace\stock_macro_news\robinhood_agent_feed\tokens.json -ErrorAction SilentlyContinue
cd C:\Users\masta\workspace
Remove-Item -Recurse -Force robinhood-for-agents
```

If you used default keychain instead of file store: Credential Manager → Windows Credentials → delete `robinhood-for-agents` entries.
