/**
 * Consolidated market context from sibling feeds (S3 + DynamoDB).
 */
import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import { GetObjectCommand, S3Client } from "@aws-sdk/client-s3";
import {
  DynamoDBDocumentClient,
  GetCommand,
  QueryCommand,
} from "@aws-sdk/lib-dynamodb";

const s3 = new S3Client({});
const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({}), {
  marshallOptions: { removeUndefinedValues: true },
});

const ACTIVE_LLM_MODELS = ["openai-gpt-4o", "gemini-2.5-flash", "grok-4.3"];

export function marketIssueDate() {
  const et = new Date(
    new Date().toLocaleString("en-US", { timeZone: "America/New_York" })
  );
  while (et.getDay() === 0 || et.getDay() === 6) {
    et.setDate(et.getDate() - 1);
  }
  const y = et.getFullYear();
  const m = String(et.getMonth() + 1).padStart(2, "0");
  const d = String(et.getDate()).padStart(2, "0");
  return `${y}-${m}-${d}`;
}

async function readJson(bucket, key) {
  try {
    const resp = await s3.send(new GetObjectCommand({ Bucket: bucket, Key: key }));
    const body = await resp.Body?.transformToString();
    return body ? JSON.parse(body) : null;
  } catch (err) {
    if (err?.name === "NoSuchKey" || err?.$metadata?.httpStatusCode === 404) return null;
    throw err;
  }
}

async function loadMacro(bucket, issueDate, slot) {
  if (!bucket) return null;
  const digestKey = `v1/date=${issueDate}/slot=${slot}/digest.json`;
  const dedupedKey = `v1/date=${issueDate}/slot=${slot}/deduped.json`;
  const digest = await readJson(bucket, digestKey);
  const deduped = await readJson(bucket, dedupedKey);
  if (!digest && !deduped) return { error: `missing s3://${bucket}/${digestKey}` };
  return {
    market_bias: digest?.digest?.market_bias,
    digest_markdown: (digest?.digest_markdown || "").slice(0, 3500),
    watchlist: (digest?.digest?.ticker_watchlist || []).slice(0, 20),
    headlines: (deduped?.articles || []).slice(0, 10).map((a) => ({
      title: a.title || a.headline || "",
      source: a.source || "",
    })),
  };
}

async function loadRotation(table, issueDate) {
  if (!table) return null;
  const resp = await ddb.send(
    new GetCommand({
      TableName: table,
      Key: { pk: `ISSUE#${issueDate}`, sk: "REPORT" },
    })
  );
  const payload = resp.Item?.payload;
  if (!payload) return { error: `no rotation report for ${issueDate}` };
  return {
    trade_date: payload.trade_date,
    spy_returns: payload.spy_returns,
    in_sectors: payload.in_sectors || [],
    out_sectors: payload.out_sectors || [],
    top_sectors: (payload.sectors || []).slice(0, 11),
    drill_down: (payload.drill_down || []).slice(0, 2),
  };
}

async function loadLlmSentiment(table, issueDate) {
  if (!table) return null;
  const resp = await ddb.send(
    new QueryCommand({
      TableName: table,
      KeyConditionExpression: "pk = :pk",
      ExpressionAttributeValues: { ":pk": `ISSUE#${issueDate}` },
    })
  );
  const items = (resp.Items || []).filter((i) => String(i.sk || "").startsWith("MODEL#"));
  const byModel = {};
  for (const item of items) {
    const mid = item.model_id || "";
    if (!ACTIVE_LLM_MODELS.includes(mid)) continue;
    const prev = byModel[mid];
    if (!prev || item.status === "ok") byModel[mid] = item;
  }
  const models = Object.entries(byModel).map(([id, item]) => ({
    model_id: id,
    market_bias: item.market_bias,
    status: item.status,
    picks: (item.picks || []).slice(0, 8).map((p) => ({
      ticker: p.ticker,
      direction: p.direction,
      conviction: p.conviction,
      rationale: (p.rationale || "").slice(0, 160),
    })),
  }));
  if (!models.length) return { error: `no LLM panel rows for ${issueDate}` };
  return { models };
}

async function loadInfluencer(table, issueDate, userId = "default") {
  if (!table) return null;
  const pk = `USER#${userId}`;
  const resp = await ddb.send(
    new GetCommand({
      TableName: table,
      Key: { pk, sk: `ISSUE#${issueDate}` },
    })
  );
  const issue = resp.Item;
  if (!issue) return { error: `no influencer issue for ${issueDate}` };
  return {
    global_summary: (issue.global_summary_smol || issue.global_summary_shift || "").slice(0, 1200),
    catalysts: (issue.global_catalysts || "").slice(0, 600),
    ticker_focus: (issue.global_ticker_focus || []).slice(0, 15),
  };
}

async function loadMarketPulse(table, issueDate) {
  if (!table) return null;
  const [signals, newsletter] = await Promise.all([
    ddb.send(
      new GetCommand({
        TableName: table,
        Key: { date: issueDate, report_type: "signals" },
      })
    ),
    ddb.send(
      new GetCommand({
        TableName: table,
        Key: { date: issueDate, report_type: "newsletter" },
      })
    ),
  ]);
  const sig = signals.Item?.payload || signals.Item;
  const news = newsletter.Item?.payload || newsletter.Item;
  if (!sig && !news) return { error: `no market-pulse for ${issueDate}` };
  return {
    ranked_sectors: (sig?.ranked_sectors || []).slice(0, 11),
    rotation_signals: (sig?.rotation_signals || []).slice(0, 6),
    cluster_signals: (sig?.cluster_signals || []).slice(0, 4),
    regime: sig?.regime,
    newsletter_excerpt: typeof news?.body === "string" ? news.body.slice(0, 1500) : "",
  };
}

function buildConfluence(macro, llm, influencer) {
  /** @type {Map<string, { long: string[], short: string[] }>} */
  const map = new Map();
  const add = (ticker, dir, source) => {
    const t = String(ticker || "").toUpperCase();
    if (!t) return;
    if (!map.has(t)) map.set(t, { long: [], short: [] });
    const row = map.get(t);
    if (dir === "short") row.short.push(source);
    else row.long.push(source);
  };

  for (const row of macro?.watchlist || []) {
    const bias = (row.bias || "").toLowerCase();
    const dir = bias.includes("bear") || bias.includes("short") ? "short" : "long";
    add(row.ticker, dir, "macro");
  }
  for (const m of llm?.models || []) {
    for (const p of m.picks || []) {
      const dir = (p.direction || "long").toLowerCase();
      add(p.ticker, dir.includes("short") ? "short" : "long", `llm:${m.model_id}`);
    }
  }
  for (const row of influencer?.ticker_focus || []) {
    const c = (row.consensus || "").toLowerCase();
    if (c.includes("bear") || c.includes("short")) add(row.ticker, "short", "influencer");
    else if (c.includes("bull") || c.includes("long")) add(row.ticker, "long", "influencer");
  }

  const rows = [];
  for (const [ticker, v] of map) {
    const l = v.long.length;
    const s = v.short.length;
    let tier = "solo";
    if (l > 0 && s > 0) tier = "split";
    else if (l >= 2 || s >= 2) tier = "confluence";
    rows.push({ ticker, long: l, short: s, tier, sources: { long: v.long, short: v.short } });
  }
  rows.sort((a, b) => b.long + b.short - (a.long + a.short));
  return rows.slice(0, 25);
}

function formatSection(title, data) {
  if (!data) return "";
  if (data.error) return `### ${title}\n_${data.error}_`;
  return `### ${title}\n\`\`\`json\n${JSON.stringify(data, null, 2).slice(0, 6000)}\n\`\`\``;
}

/**
 * @param {ReturnType<import("./config.js").loadConfig>} cfg
 */
export async function buildMarketContext(cfg) {
  const issueDate = cfg.contextIssueDate || marketIssueDate();
  const slot = cfg.contextSlot || "pre_open";

  const [macro, rotation, llm, influencer, pulse] = await Promise.all([
    loadMacro(cfg.macroBucket, issueDate, slot),
    loadRotation(cfg.rotationTable, issueDate),
    loadLlmSentiment(cfg.llmSentimentTable, issueDate),
    loadInfluencer(cfg.influencerTable, issueDate, cfg.influencerUserId),
    loadMarketPulse(cfg.marketPulseTable, issueDate),
  ]);

  const confluence = buildConfluence(macro, llm, influencer);

  const parts = [
    `## Cross-feed intelligence (${issueDate}, slot=${slot})`,
    "",
    "_Optional background from our daily pipelines — one input among many; use your own judgment._",
    "",
    formatSection("Sector rotation (sector_rotation_feed)", rotation),
    formatSection("Macro digest + watchlist (macro_news_feed)", macro),
    formatSection("LLM sentiment panel (llm_sentiment_feed)", llm),
    formatSection("Influencer digest (influencer_feed)", influencer),
    formatSection("Market pulse signals (market-pulse)", pulse),
    "### Ticker confluence (macro + LLM + influencer)",
    confluence.length
      ? confluence
          .map(
            (r) =>
              `- **${r.ticker}** [${r.tier}] long=${r.long} short=${r.short} (${[...r.sources.long, ...r.sources.short].join(", ")})`
          )
          .join("\n")
      : "_No confluence — feeds may be empty for this date._",
  ];

  const loaded = [macro, rotation, llm, influencer, pulse].filter((x) => x && !x.error);
  if (!loaded.length) {
    parts.push(
      "",
      "_No feed data loaded. Set MACRO_ARTIFACTS_BUCKET and feed table env vars in .env / Lambda._"
    );
  }

  return parts.join("\n");
}
