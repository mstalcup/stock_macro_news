import { GetSecretValueCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";

const sm = new SecretsManagerClient({});

/**
 * @param {string} secretArn
 */
export async function loadDiscordToken(secretArn) {
  const raw = await sm.send(new GetSecretValueCommand({ SecretId: secretArn }));
  const data = JSON.parse(raw.SecretString || "{}");
  const token = (data.bot_token || data.token || "").trim();
  if (!token) throw new Error("Missing bot_token in discord-bot secret");
  return token;
}

/**
 * @param {string} secretArn
 */
export async function loadDiscordConfig(secretArn) {
  const raw = await sm.send(new GetSecretValueCommand({ SecretId: secretArn }));
  const data = JSON.parse(raw.SecretString || "{}");
  const token = (data.bot_token || data.token || "").trim();
  const channelId = (data.channel_id || "").trim();
  if (!token) throw new Error("Missing bot_token in discord-bot secret");
  return { token, channelId };
}

/**
 * @param {string} token
 * @param {string} channelId
 * @param {string} content
 */
export async function postDiscordMessage(token, channelId, content) {
  if (!channelId) {
    console.log("No DISCORD channel — digest:\n", content);
    return { skipped: true };
  }
  const chunks = splitDiscord(content, 1900);
  const ids = [];
  for (const chunk of chunks) {
    const resp = await fetch(`https://discord.com/api/v10/channels/${channelId}/messages`, {
      method: "POST",
      headers: {
        Authorization: `Bot ${token}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ content: chunk }),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Discord post failed ${resp.status}: ${text.slice(0, 300)}`);
    }
    const data = await resp.json();
    ids.push(data.id);
  }
  return { message_ids: ids };
}

function splitDiscord(text, max) {
  if (text.length <= max) return [text];
  const parts = [];
  let rest = text;
  while (rest.length > max) {
    let cut = rest.lastIndexOf("\n", max);
    if (cut < max / 2) cut = max;
    parts.push(rest.slice(0, cut));
    rest = rest.slice(cut);
  }
  if (rest) parts.push(rest);
  return parts;
}

/**
 * @param {object} digest
 */
export function formatDigest(digest) {
  const lines = [
    `**Robinhood Agent — ${digest.run_id}**`,
    digest.thesis ? `**Thesis:** ${digest.thesis}` : "",
    "",
    "**Hold**",
    ...(digest.hold?.length ? digest.hold.map((h) => `- ${h}`) : ["- (none)"]),
    "",
    "**Close**",
    ...(digest.close?.length ? digest.close.map((c) => `- ${c}`) : ["- (none)"]),
    "",
    "**Open**",
    ...(digest.open?.length ? digest.open.map((o) => `- ${o}`) : ["- (none)"]),
    "",
    "**Executed**",
    ...(digest.executed?.length ? digest.executed.map((e) => `- ${e}`) : ["- (dry run / no trades)"]),
  ];
  if (digest.notes) lines.push("", `_${digest.notes}_`);
  if (digest.error) lines.push("", `⚠️ ${digest.error}`);
  return lines.filter((l) => l !== undefined).join("\n");
}
