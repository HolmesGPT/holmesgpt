// Node 18+
// NOTE: Using CommonJS instead of ES modules due to New Relic Kubernetes operator limitations.
// The operator currently injects instrumentation using --require which only works with CommonJS.
// ES modules would need --loader/--experimental-loader support which is not yet available.
const express = require("express");
const crypto = require("node:crypto");
const pino = require("pino");
const { Pool } = require("pg");

// ----- config -----
const PORT = process.env.PORT || 3000;
const INVENTORY_BASE_URL = process.env.INVENTORY_BASE_URL || "http://localhost:7000";
const RISK_BASE_URL = process.env.RISK_BASE_URL || "http://localhost:8000";
const TIMEOUT_MS = Number(process.env.TIMEOUT_MS || 400);
// Build DSN from individual components since K8s doesn't do variable substitution
const PG_DSN = `postgresql://${process.env.POSTGRES_USER}:${process.env.POSTGRES_PASSWORD}@postgres.app-117.svc.cluster.local:5432/${process.env.POSTGRES_DB}`;

// logging (off by default unless LOG_LEVEL or LOG=1)
const level = process.env.LOG_LEVEL || (process.env.LOG === "1" ? "info" : "silent");
const log = pino({ level });

// db pool (pg auto-instrumented by NR agent if present)
const pool = new Pool({ connectionString: PG_DSN });

// ----- utils -----
async function fetchJson(url, { method = "GET", headers = {}, body, timeout = TIMEOUT_MS } = {}) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), timeout);
  try {
    const res = await fetch(url, {
      method,
      headers: { "content-type": "application/json", ...headers },
      body: body ? JSON.stringify(body) : undefined,
      signal: ctrl.signal
    });
    const json = await res.json().catch(() => ({}));
    return { status: res.status, json };
  } finally {
    clearTimeout(t);
  }
}

// optional hooks (for demo tracing/logging of internal phases)
async function preInventory(ctx)  { log.debug({ ctx }, "preInventory"); }
async function postInventory(ctx) { log.debug({ ctx }, "postInventory"); }
async function preRisk(ctx)       { log.debug({ ctx }, "preRisk"); }
async function postRisk(ctx)      { log.debug({ ctx }, "postRisk"); }

// ----- server -----
const app = express();
app.use(express.json());

// Health check
app.get("/healthz", function healthCheck(req, res) {
  res.json({ ok: true, service: "checkout" });
});

// Main order processing endpoint
app.post("/orders", async function processOrder(req, res) {
  try {
    const body = req.body;
    const requestId = req.headers["x-request-id"] || crypto.randomUUID();

    // Set the correlation header immediately for all response paths
    res.setHeader("x-request-id", requestId);

    // forward correlation header
    const fwd = {
      "x-request-id": requestId
    };

    const ctx = {
      requestId,
      userId: body.userId || "anon",
      itemId: body.itemId || "sku-1",
      qty: Number(body.qty ?? 1),
      amount: Number(body.amount ?? 0)
    };
    log.info({ path: "/orders", ctx }, "received");

    // 1) inventory
    await preInventory(ctx);
    const inv = await fetchJson(new URL("/inventory/check", INVENTORY_BASE_URL), {
      method: "POST",
      headers: fwd,
      body: { itemId: ctx.itemId, qty: ctx.qty }
    });
    await postInventory({ ...ctx, invStatus: inv.status });
    if (inv.status !== 200 || !inv.json?.available) {
      return res.status(409).json({ ok: false, stage: "inventory", reason: "not_available" });
    }

    // 2) risk
    await preRisk(ctx);
    const risk = await fetchJson(new URL("/risk/score", RISK_BASE_URL), {
      method: "POST",
      headers: fwd,
      body: { userId: ctx.userId, amount: ctx.amount, itemId: ctx.itemId, qty: ctx.qty }
    });
    await postRisk({ ...ctx, riskStatus: risk.status, score: risk.json?.score });
    if (risk.status !== 200 || risk.json?.isFraud) {
      return res.status(403).json({ ok: false, stage: "risk", reason: "suspected_fraud", score: risk.json?.score });
    }

    // 3) persist order (single INSERT)
    try {
      await pool.query(
        "INSERT INTO orders (order_id, user_id, item_id, qty, amount, status) VALUES ($1,$2,$3,$4,$5,$6)",
        [requestId, ctx.userId, ctx.itemId, ctx.qty, ctx.amount, "PLACED"]
      );
      log.info({ orderId: requestId }, "order insertepleased");
    } catch (dbErr) {
      log.error({ err: dbErr?.message || dbErr }, "db insert failed");
      return res.status(500).json({ ok: false, stage: "db", error: String(dbErr?.message || dbErr) });
    }

    // success
    res.json({
      ok: true,
      orderId: requestId,
      inventory: { available: true },
      risk: { isFraud: false, score: risk.json?.score ?? 0 },
      status: "PLACED"
    });
  } catch (e) {
    log.error({ err: e?.message || String(e) }, "unhandled");
    res.status(500).json({ ok: false, stage: "unknown", error: String(e) });
  }
});

// 404 handler
app.use((req, res) => {
  res.status(404).json({ error: "not_found" });
});

// Error handler
app.use((err, req, res, next) => {
  log.error({ err: err?.message || String(err) }, "unhandled");
  res.status(500).json({ ok: false, stage: "unknown", error: String(err) });
});

// Graceful shutdown
process.on("SIGTERM", async () => {
  log.info("shutdown");
  try { await pool.end(); } catch {}
  server.close(() => process.exit(0));
});

const server = app.listen(PORT, () => log.info({ port: PORT }, "listening"));
