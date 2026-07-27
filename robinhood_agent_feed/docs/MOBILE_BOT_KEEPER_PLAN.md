# Mobile “Bot Keeper” — plan (pick up later)

**Goal:** A lightweight phone UI you open when using Robinhood day-to-day. One tap refreshes OAuth tokens and uploads them to AWS so the scheduled Lambda agent stays connected—without laptop DevTools.

**Status:** Planned (not implemented). Depends on a one-time desktop bootstrap with `device_token` — see [OAUTH_BOOTSTRAP.md](./OAUTH_BOOTSTRAP.md).

---

## Problem

- Agentic MCP OAuth needs `access_token`, `refresh_token`, and `device_token`.
- Lambda auto-refreshes access tokens for ~8.5 days **only if** `device_token` is in Secrets Manager.
- When refresh fails, the agent is down until manual re-bootstrap.
- The official Robinhood **phone app** cannot share its session (sandboxed, different OAuth client).

## Solution (high level)

A **companion app or PWA** (not reading Robinhood’s app):

1. Shows connection status (“Bot OK until …”).
2. Toggle **“Keep bot on”** → enable expiry notifications.
3. **“Refresh connection”** → system browser OAuth for MCP → upload tokens to AWS.

User habit: see yellow/red status or push → tap refresh (~10s) while already checking markets on phone.

---

## Architecture

```
┌─────────────────┐     HTTPS + auth      ┌──────────────────┐
│  Bot Keeper     │ ────────────────────► │ API Gateway      │
│  (iOS/Android/  │   POST /rh-tokens     │ + Lambda         │
│   PWA)          │   GET  /rh-status     └────────┬─────────┘
└────────┬────────┘                              │
         │ OAuth (system browser)                 ▼
         ▼                              ┌──────────────────┐
┌─────────────────┐                     │ Secrets Manager  │
│ Robinhood MCP   │                     │ …/rh-oauth       │
│ OAuth           │                     └────────┬─────────┘
└─────────────────┘                              │
                                                 ▼
                                      ┌──────────────────┐
                                      │ run-agent Lambda │
                                      │ (existing stack) │
                                      └──────────────────┘

Optional:
  EventBridge (daily) → check-rh-token Lambda → SNS push if expires_at < 48h
```

### AWS components (new, in `robinhood_agent_feed` SAM stack or small sibling stack)

| Resource | Purpose |
|----------|---------|
| `RhTokenApiFunction` | `POST /rh-tokens` — validate body, optional test refresh, `PutSecretValue` |
| `RhTokenStatusFunction` | `GET /rh-status` — return `expires_at`, days left, `device_token` present (no secrets in response) |
| `RhTokenCheckFunction` (optional) | Daily cron; SNS/email/Discord if expiry soon |
| API Gateway HTTP API | Routes + CORS for PWA |
| Cognito User Pool **or** API key in Secrets Manager | Authenticate phone uploads only |

Reuse existing secret: `robinhood-agent-feed/rh-oauth`.

### Token upload payload

Same JSON as `tokens.json` / bootstrap:

```json
{
  "access_token": "...",
  "refresh_token": "...",
  "device_token": "...",
  "expires_at": "2026-07-16T12:00:00.000Z",
  "user_uuid": "..."
}
```

Server-side after upload:

1. Validate required fields.
2. Optionally call Robinhood `/oauth2/token/` refresh once to verify pair works.
3. Write to Secrets Manager.
4. Return `{ ok: true, expires_at, days_remaining }`.

---

## Mobile OAuth flow (client)

**Cannot** read Robinhood app auth. **Can** run MCP OAuth like desktop:

1. On first connect, generate and persist `device_token` (UUID v4) in app secure storage.
2. PKCE: generate `code_verifier` / `code_challenge`.
3. Open **ASWebAuthenticationSession** (iOS) or **Chrome Custom Tab** (Android) to Robinhood authorize URL for MCP client.
4. Redirect to app universal link / custom scheme with `code`.
5. Exchange `code` at `https://api.robinhood.com/oauth2/token/` including `device_token`, `client_id`, `code_verifier`.
6. Store tokens locally (Keychain/Keystore) + `POST` to `/rh-tokens`.

**Open research** (before coding): confirm exact MCP OAuth metadata URL and `client_id` for a custom mobile client vs reusing Claude’s client id. May need to register redirect URI with Robinhood or use a fixed HTTPS callback on your domain.

Fallback for v0: PWA opens Robinhood OAuth in browser; user pastes tokens from a minimal “paste upload” form (worse UX, no store review).

---

## UX spec (v1)

| Screen | Content |
|--------|---------|
| Home | Status chip: green / yellow (&lt;48h) / red (expired or missing) |
| Toggle | “Keep bot on” — enables push when yellow/red |
| Primary CTA | “Refresh connection” — starts OAuth |
| Secondary | “Last synced: …” / “Agent last run: …” (from `/rh-status` + optional DynamoDB) |

**Notifications (v1.1):** SNS → APNs/FCM when `expires_at - now < 48h` and toggle is on.

**Do not:** try to detect when Robinhood app opens (iOS forbids; Android fragile).

---

## Security

- Upload API must require auth (Cognito preferred over hardcoded API key in app binary).
- HTTPS only; rate limit `POST /rh-tokens`.
- Status endpoint returns metadata only — never tokens.
- Audit log: CloudWatch + optional DynamoDB `AUTH#sync` rows.
- Document threat model: stolen phone + unlocked app could push tokens — use biometric lock on Bot Keeper.

---

## Implementation phases

### Phase 0 — Unblock agent (now)

- [ ] Complete [OAUTH_BOOTSTRAP.md](./OAUTH_BOOTSTRAP.md) with `device_token`
- [ ] Confirm scheduled runs succeed in CloudWatch

### Phase 1 — Token upload API (backend only)

- [ ] Add `lambdas/rh_token_api/` handler
- [ ] SAM: API Gateway routes, IAM for `secretsmanager:PutSecretValue` on `rh-oauth`
- [ ] Auth: start with API key in header (`X-Bot-Keeper-Key`) stored in Secrets Manager; migrate to Cognito later
- [ ] `tools/upload_rh_tokens.py` CLI to test same endpoint from laptop
- [ ] README section

### Phase 2 — PWA (fastest phone UI)

- [ ] Static site (S3 + CloudFront or single Lambda URL) — status + “Refresh” stub
- [ ] If full OAuth in browser is blocked on mobile, manual paste form as interim
- [ ] Home screen bookmark on iPhone

### Phase 3 — Native or React Native shell

- [ ] Secure storage for `device_token` + Cognito login
- [ ] System browser OAuth + deep link
- [ ] Push registration for expiry alerts

### Phase 4 — Ops polish

- [ ] Daily `check-rh-token` Lambda + Discord/SNS alert (reuse discord secret)
- [ ] Dashboard: last successful agent run from DynamoDB `RUN#` summaries

---

## Repo layout (when built)

```
robinhood_agent_feed/
  docs/
    OAUTH_BOOTSTRAP.md          # done
    MOBILE_BOT_KEEPER_PLAN.md   # this file
  lambdas/
    rh_token_api/
      handler.js
      lib/validate.js
  tools/
    upload_rh_tokens.py
  mobile/                       # optional later
    pwa/
```

---

## Success criteria

- After phone refresh, `py tools/check_rh_secret.py` shows `device_token: set` and fresh `expires_at`.
- Next scheduled `run-agent` completes without `token refresh failed`.
- User can recover from red status in &lt;30 seconds on phone without a laptop.

---

## References

- [Robinhood Agentic overview](https://robinhood.com/us/en/support/articles/agentic-trading-overview/)
- [robinhood-for-agents](https://github.com/kevin1chun/robinhood-for-agents) — token refresh + `device_token` model
- Existing code: `lambdas/run_agent/lib/rh_auth.js`, `tools/bootstrap_rh_auth.py`
