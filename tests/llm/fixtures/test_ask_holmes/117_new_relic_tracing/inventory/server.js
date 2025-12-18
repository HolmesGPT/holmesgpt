// Node 18+
// NOTE: Using CommonJS instead of ES modules due to New Relic Kubernetes operator limitations.
// The operator currently injects instrumentation using --require which only works with CommonJS.
// ES modules would need --loader/--experimental-loader support which is not yet available.
const express = require("express");
const pino = require("pino");

const PORT = process.env.PORT || 7000;
const level = process.env.LOG_LEVEL || (process.env.LOG === "1" ? "info" : "silent");
const log = pino({ level });

const app = express();
app.use(express.json());

// Health check
app.get("/healthz", function healthCheck(req, res) {
  res.json({ ok: true, service: "inventory" });
});

// Inventory check endpoint
app.post("/inventory/check", function inventoryCheck(req, res) {
  const { itemId = "sku-1", qty = 1 } = req.body;
  const available = Number(qty) <= 5;
  log.info({ itemId, qty, available }, "inventory.check");
  res.json({ available });
});

// 404 handler
app.use((req, res) => {
  res.status(404).json({ error: "not_found" });
});

// Error handler
app.use((err, req, res, next) => {
  log.error({ err: err?.message || String(err) }, "unhandled");
  res.status(500).json({ error: String(err) });
});

// Graceful shutdown
process.on("SIGTERM", () => {
  log.info("shutdown");
  server.close(() => process.exit(0));
});

const server = app.listen(PORT, () => log.info({ port: PORT }, "listening"));
