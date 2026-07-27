import { DynamoDBClient } from "@aws-sdk/client-dynamodb";
import {
  DynamoDBDocumentClient,
  PutCommand,
  QueryCommand,
  ScanCommand,
  UpdateCommand,
} from "@aws-sdk/lib-dynamodb";

const ddb = DynamoDBDocumentClient.from(new DynamoDBClient({}), {
  marshallOptions: { removeUndefinedValues: true },
});

function pkRun(runId) {
  return `RUN#${runId}`;
}

function pkTrade(runId) {
  return `TRADE#${runId}`;
}

function pkPosition(symbol) {
  return `POSITION#${(symbol || "").toUpperCase()}`;
}

/**
 * @param {string} tableName
 * @param {string} runId
 * @param {Record<string, unknown>} meta
 */
export async function writeRunStart(tableName, runId, meta) {
  const now = new Date().toISOString();
  await ddb.send(
    new PutCommand({
      TableName: tableName,
      Item: {
        pk: pkRun(runId),
        sk: "META",
        run_id: runId,
        status: "running",
        started_at: now,
        ...meta,
      },
    })
  );
}

/**
 * @param {string} tableName
 * @param {string} runId
 * @param {Record<string, unknown>} summary
 */
export async function writeRunEnd(tableName, runId, summary) {
  const now = new Date().toISOString();
  await ddb.send(
    new PutCommand({
      TableName: tableName,
      Item: {
        pk: pkRun(runId),
        sk: "SUMMARY",
        run_id: runId,
        status: summary.error ? "failed" : "completed",
        finished_at: now,
        ...summary,
      },
    })
  );
}

/**
 * Full strategy ledger for symbols held in the account: original thesis + follow-on trades.
 * @param {string} tableName
 * @param {string[]} symbols upper-case tickers from MCP positions
 */
export async function loadPositionLedger(tableName, symbols = []) {
  if (!tableName) return [];
  const uniq = [...new Set(symbols.map((s) => String(s || "").toUpperCase()).filter(Boolean))];
  const bundles = [];

  for (const symbol of uniq) {
    const resp = await ddb.send(
      new QueryCommand({
        TableName: tableName,
        KeyConditionExpression: "pk = :pk",
        ExpressionAttributeValues: { ":pk": pkPosition(symbol) },
      })
    );
    const items = resp.Items || [];
    const latest = items.find((i) => i.sk === "LATEST") || null;
    const theses = items.filter((i) => String(i.sk || "").startsWith("THESIS#"));
    const thesisId = latest?.thesis_id || theses.find((t) => t.status === "open")?.thesis_id;
    const originalThesis =
      (thesisId && theses.find((t) => t.sk === `THESIS#${thesisId}`)) ||
      theses.find((t) => t.status === "open") ||
      null;
    const tradeHistory = items
      .filter((i) => String(i.sk || "").startsWith("TRADE#"))
      .sort((a, b) => String(a.timestamp || a.sk).localeCompare(String(b.timestamp || b.sk)))
      .map((t) => ({
        thesis_id: t.thesis_id,
        parent_thesis_id: t.parent_thesis_id,
        action: t.action,
        side: t.side,
        amount: t.amount,
        strategy: t.strategy,
        justification: t.justification,
        entry_level: t.entry_level,
        stop_level: t.stop_level,
        exit_target: t.exit_target,
        run_id: t.run_id,
        timestamp: t.timestamp,
        status: t.status,
      }));

    bundles.push({
      symbol,
      latest,
      original_thesis: originalThesis,
      trade_history: tradeHistory,
    });
  }

  return bundles;
}

/**
 * Open position summaries (LATEST rows) — used when MCP positions are empty.
 * @param {string} tableName
 */
export async function loadActiveTheses(tableName) {
  if (!tableName) return [];
  const items = [];
  let lastKey;
  do {
    const resp = await ddb.send(
      new ScanCommand({
        TableName: tableName,
        FilterExpression: "begins_with(pk, :pfx) AND sk = :latest AND #st = :open",
        ExpressionAttributeNames: { "#st": "status" },
        ExpressionAttributeValues: {
          ":pfx": "POSITION#",
          ":latest": "LATEST",
          ":open": "open",
        },
        ExclusiveStartKey: lastKey,
      })
    );
    items.push(...(resp.Items || []));
    lastKey = resp.LastEvaluatedKey;
  } while (lastKey);
  return items;
}

/**
 * Persist a trade + update position thesis ledger.
 * @param {string} tableName
 * @param {string} runId
 * @param {Record<string, unknown>} trade
 */
export async function recordTradeEvent(tableName, runId, trade) {
  const symbol = String(trade.symbol || "").toUpperCase();
  const ts = String(trade.timestamp || new Date().toISOString());
  const action = String(trade.action || (trade.side === "sell" ? "close" : "open"));
  const status = String(trade.status || "submitted");
  const persistThesis = status === "submitted";
  const thesisId =
    String(trade.thesis_id || "") ||
    (action === "open" ? `${runId}-${symbol}` : "");
  const rootThesisId = String(trade.parent_thesis_id || trade.thesis_id || thesisId || "");
  const id = String(trade.id || `${Date.now()}-${symbol}`);

  const row = {
    symbol,
    run_id: runId,
    thesis_id: thesisId,
    parent_thesis_id: rootThesisId || thesisId,
    root_thesis_id: rootThesisId || thesisId,
    action,
    side: trade.side,
    amount: trade.amount,
    justification: trade.justification || trade.reason || "",
    strategy: trade.strategy || "",
    entry_level: trade.entry_level ?? null,
    stop_level: trade.stop_level ?? null,
    exit_target: trade.exit_target ?? null,
    status,
    timestamp: ts,
    review: trade.review || null,
    placed: trade.placed || null,
    fully_exited: Boolean(trade.fully_exited),
  };

  await ddb.send(
    new PutCommand({
      TableName: tableName,
      Item: {
        pk: pkTrade(runId),
        sk: `ORDER#${ts}#${id}`,
        ...row,
      },
    })
  );

  if (!persistThesis) {
    return;
  }

  if (symbol) {
    await ddb.send(
      new PutCommand({
        TableName: tableName,
        Item: {
          pk: pkPosition(symbol),
          sk: `TRADE#${ts}#${id}`,
          ...row,
        },
      })
    );
  }

  if (action === "open" && symbol && thesisId) {
    const thesis = {
      pk: pkPosition(symbol),
      sk: `THESIS#${thesisId}`,
      symbol,
      thesis_id: thesisId,
      root_thesis_id: thesisId,
      status: "open",
      strategy: row.strategy,
      justification: row.justification,
      entry_level: row.entry_level,
      stop_level: row.stop_level,
      exit_target: row.exit_target,
      opened_at: ts,
      opened_run_id: runId,
      closed_at: null,
      closed_run_id: null,
    };
    await ddb.send(new PutCommand({ TableName: tableName, Item: thesis }));
    await ddb.send(
      new PutCommand({
        TableName: tableName,
        Item: {
          pk: pkPosition(symbol),
          sk: "LATEST",
          symbol,
          status: "open",
          thesis_id: thesisId,
          root_thesis_id: thesisId,
          strategy: row.strategy,
          justification: row.justification,
          entry_level: row.entry_level,
          stop_level: row.stop_level,
          exit_target: row.exit_target,
          opened_at: ts,
          updated_at: ts,
          last_run_id: runId,
        },
      })
    );
  } else if (symbol && thesisId && action !== "open") {
    const status = row.fully_exited ? "closed_exited" : "open";
    await ddb.send(
      new UpdateCommand({
        TableName: tableName,
        Key: { pk: pkPosition(symbol), sk: "LATEST" },
        UpdateExpression:
          "SET updated_at = :ts, last_run_id = :run, last_action = :act, last_justification = :j, #st = :st",
        ExpressionAttributeNames: { "#st": "status" },
        ExpressionAttributeValues: {
          ":ts": ts,
          ":run": runId,
          ":act": action,
          ":j": row.justification,
          ":st": status,
        },
      })
    );
    if (row.fully_exited) {
      await ddb.send(
        new UpdateCommand({
          TableName: tableName,
          Key: { pk: pkPosition(symbol), sk: `THESIS#${thesisId}` },
          UpdateExpression:
            "SET #st = :closed, closed_at = :ts, closed_run_id = :run, exit_justification = :j",
          ExpressionAttributeNames: { "#st": "status" },
          ExpressionAttributeValues: {
            ":closed": "closed_exited",
            ":ts": ts,
            ":run": runId,
            ":j": row.justification,
          },
        })
      );
    }
  }
}

export function newRunId() {
  const d = new Date();
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getUTCFullYear()}${pad(d.getUTCMonth() + 1)}${pad(d.getUTCDate())}-${pad(d.getUTCHours())}${pad(d.getUTCMinutes())}${pad(d.getUTCSeconds())}`;
}
