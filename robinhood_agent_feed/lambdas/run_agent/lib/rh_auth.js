import {
  GetSecretValueCommand,
  PutSecretValueCommand,
  SecretsManagerClient,
} from "@aws-sdk/client-secrets-manager";
import { RH_CLIENT_ID, RH_TOKEN_URL } from "./config.js";

const sm = new SecretsManagerClient({});

// Note: do not send expires_in on refresh — it yields JWTs without meta.oid that MCP rejects.

/**
 * @param {string} secretArn
 * @returns {Promise<import("./config.js").RhTokens>}
 */
export async function loadTokens(secretArn) {
  const raw = await sm.send(new GetSecretValueCommand({ SecretId: secretArn }));
  const data = JSON.parse(raw.SecretString || "{}");
  return {
    access_token: (data.access_token || "").trim(),
    refresh_token: (data.refresh_token || "").trim(),
    device_token: (data.device_token || "").trim(),
    expires_at: data.expires_at || "",
    user_uuid: data.user_uuid || "",
    client_id: (data.client_id || "").trim(),
  };
}

/**
 * @param {string} secretArn
 * @param {import("./config.js").RhTokens} tokens
 */
export async function saveTokens(secretArn, tokens) {
  await sm.send(
    new PutSecretValueCommand({
      SecretId: secretArn,
      SecretString: JSON.stringify(tokens),
    })
  );
}

function expiresSoon(expiresAt) {
  if (!expiresAt) return true;
  const t = Date.parse(expiresAt);
  if (Number.isNaN(t)) return true;
  return t - Date.now() < 24 * 60 * 60 * 1000;
}

function resolveClientId(tokens) {
  const id = (tokens.client_id || RH_CLIENT_ID || "").trim();
  if (!id) {
    throw new Error(
      "Missing client_id in rh-oauth secret — re-export MCP tokens (Claude/Cursor) so client_id is stored"
    );
  }
  return id;
}

/**
 * Refresh access token.
 * Claude/Cursor MCP clients refresh with refresh_token + client_id only.
 * Web/app clients also need device_token (and are rejected by Trading MCP anyway).
 *
 * @param {import("./config.js").RhTokens} tokens
 * @returns {Promise<import("./config.js").RhTokens>}
 */
export async function refreshTokens(tokens) {
  if (!tokens.refresh_token) {
    throw new Error("Missing refresh_token — re-bootstrap Robinhood OAuth");
  }
  const clientId = resolveClientId(tokens);
  // Do NOT send expires_in — it returns a JWT without meta.oid that MCP rejects
  // ("client id not allowed: <missing>"). Minimal refresh preserves Claude/Cursor client binding.
  const body = new URLSearchParams({
    grant_type: "refresh_token",
    refresh_token: tokens.refresh_token,
    client_id: clientId,
  });
  if (tokens.device_token) {
    body.set("device_token", tokens.device_token);
  }
  const resp = await fetch(RH_TOKEN_URL, {
    method: "POST",
    headers: {
      "Content-Type": "application/x-www-form-urlencoded; charset=utf-8",
      "X-Robinhood-API-Version": "1.431.4",
    },
    body: body.toString(),
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`Robinhood token refresh failed ${resp.status}: ${text.slice(0, 300)}`);
  }
  const data = await resp.json();
  const expiresIn = Number(data.expires_in || 0);
  const expires_at = expiresIn
    ? new Date(Date.now() + expiresIn * 1000).toISOString()
    : tokens.expires_at;
  return {
    access_token: data.access_token || tokens.access_token,
    refresh_token: data.refresh_token || tokens.refresh_token,
    device_token: tokens.device_token || "",
    expires_at,
    user_uuid: data.user_uuid || tokens.user_uuid,
    client_id: clientId,
  };
}

/**
 * @param {string} secretArn
 * @returns {Promise<import("./config.js").RhTokens>}
 */
export async function getValidTokens(secretArn) {
  let tokens = await loadTokens(secretArn);
  if (!tokens.access_token) {
    throw new Error("Missing Robinhood OAuth tokens — run tools/bootstrap_rh_auth.py");
  }
  if (expiresSoon(tokens.expires_at)) {
    if (!tokens.refresh_token) {
      if (!tokens.expires_at || Date.parse(tokens.expires_at) <= Date.now()) {
        throw new Error("Access token expired and no refresh_token available");
      }
      return tokens;
    }
    tokens = await refreshTokens(tokens);
    await saveTokens(secretArn, tokens);
  }
  return tokens;
}

/**
 * MCP SDK OAuthClientProvider used by StreamableHTTPClientTransport.
 *
 * @param {import("./config.js").RhTokens} tokens
 * @param {string} secretArn
 */
export function createAuthProvider(tokens, secretArn) {
  let current = { ...tokens };
  let refreshing = null;
  let codeVerifier = "";

  async function doRefresh() {
    current = await refreshTokens(current);
    await saveTokens(secretArn, current);
  }

  async function ensureFresh() {
    if (current.refresh_token && expiresSoon(current.expires_at)) {
      if (!refreshing) {
        refreshing = doRefresh().finally(() => {
          refreshing = null;
        });
      }
      await refreshing;
    }
  }

  const clientId = () => (current.client_id || RH_CLIENT_ID || "").trim();

  return {
    get redirectUrl() {
      return undefined;
    },
    get clientMetadata() {
      return {
        client_name: "robinhood-agent-feed",
        redirect_uris: [],
        grant_types: ["refresh_token"],
        response_types: [],
        token_endpoint_auth_method: "none",
      };
    },
    async clientInformation() {
      const id = clientId();
      return id ? { client_id: id } : undefined;
    },
    async tokens() {
      await ensureFresh();
      if (!current.access_token) return undefined;
      const out = {
        access_token: current.access_token,
        token_type: "Bearer",
      };
      if (current.refresh_token) {
        out.refresh_token = current.refresh_token;
      }
      return out;
    },
    async saveTokens(newTokens) {
      current = {
        ...current,
        access_token: newTokens.access_token || current.access_token,
        refresh_token: newTokens.refresh_token || current.refresh_token,
        expires_at: newTokens.expires_in
          ? new Date(Date.now() + Number(newTokens.expires_in) * 1000).toISOString()
          : current.expires_at,
        client_id: current.client_id || clientId(),
      };
      await saveTokens(secretArn, current);
    },
    async invalidateCredentials(scope) {
      if ((scope === "tokens" || scope === "all") && current.refresh_token) {
        if (!refreshing) {
          refreshing = doRefresh().finally(() => {
            refreshing = null;
          });
        }
        await refreshing;
      }
    },
    async redirectToAuthorization() {
      throw new Error(
        "Robinhood MCP interactive OAuth is not supported in Lambda — re-export tokens from Claude/Cursor MCP"
      );
    },
    async saveCodeVerifier(verifier) {
      codeVerifier = verifier || "";
    },
    async codeVerifier() {
      return codeVerifier;
    },
    /** Prefer our refresh_token grant when the SDK runs non-interactive auth. */
    async prepareTokenRequest() {
      if (!current.refresh_token) return undefined;
      const params = new URLSearchParams({
        grant_type: "refresh_token",
        refresh_token: current.refresh_token,
        client_id: clientId(),
      });
      if (current.device_token) {
        params.set("device_token", current.device_token);
      }
      return params;
    },
    async addClientAuthentication(_headers, params) {
      if (current.device_token && !params.has("device_token")) {
        params.set("device_token", current.device_token);
      }
      // Never inject expires_in — breaks MCP client binding on the JWT.
      params.delete("expires_in");
      const id = clientId();
      if (id && !params.has("client_id")) {
        params.set("client_id", id);
      }
    },
    getTokens() {
      return current;
    },
  };
}

export async function loadAgentKeys(secretArn) {
  const raw = await sm.send(new GetSecretValueCommand({ SecretId: secretArn }));
  const data = JSON.parse(raw.SecretString || "{}");
  const anthropic = (data.anthropic_api_key || "").trim();
  const finnhub = (data.finnhub_api_key || "").trim();
  if (!anthropic) {
    throw new Error("Missing anthropic_api_key in agent-keys secret");
  }
  return { anthropic_api_key: anthropic, finnhub_api_key: finnhub };
}
