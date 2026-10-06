import { pathToFileURL } from "node:url";
import express from "express";
import { requirePartner } from "./auth.js";
import { errorHandler } from "./errors.js";
import { createPartnerRegistry } from "./partners.js";
import { createSandboxRouter } from "./sandbox.js";
import { createShipmentService, createShipmentsRouter } from "./shipments.js";
import { createWebhookDispatcher, createWebhooksRouter } from "./webhooks.js";

function requestLogger(request, response, next) {
  const startedAt = performance.now();
  response.on("finish", () => {
    const elapsed = Math.round(performance.now() - startedAt);
    console.log(`${request.method} ${request.originalUrl} ${response.statusCode} ${elapsed}ms`);
  });
  next();
}

function routeNotFound(request, response) {
  response
    .status(404)
    .json({ code: "NOT_FOUND", message: `No route for ${request.method} ${request.path}` });
}

export function createApp({
  sandbox = false,
  log = false,
  webhookRetryDelaysMs,
  fetchImpl,
  clock,
} = {}) {
  const partners = createPartnerRegistry();
  const webhooks = createWebhookDispatcher({
    retryDelaysMs: webhookRetryDelaysMs,
    fetchImpl,
    log: log ? (message) => console.warn(message) : undefined,
  });
  const shipments = createShipmentService({ partners, webhooks, clock });

  const app = express();
  app.disable("x-powered-by");
  app.locals.parcelly = { partners, shipments, webhooks };

  if (log) {
    app.use(requestLogger);
  }
  app.use(express.json({ limit: "100kb" }));

  app.get("/health", (request, response) => {
    response.json({ status: "ok", sandbox });
  });
  app.use(
    "/v1",
    requirePartner(partners),
    createShipmentsRouter({ shipments }),
    createWebhooksRouter({ partners }),
  );
  if (sandbox) {
    app.use("/__sandbox", createSandboxRouter({ partners, shipments, webhooks }));
  }

  app.use(routeNotFound);
  app.use(errorHandler);
  return app;
}

function isMainModule() {
  return process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href;
}

if (isMainModule()) {
  const port = Number(process.env.PORT ?? 8080);
  const host = process.env.HOST ?? "0.0.0.0";
  const sandbox = process.env.SANDBOX === "1";
  const server = createApp({ sandbox, log: true }).listen(port, host, (error) => {
    if (error) {
      throw error;
    }
    console.log(`Parcelly listening on http://${host}:${port}${sandbox ? " (sandbox mode)" : ""}`);
  });
  process.on("SIGTERM", () => server.close(() => process.exit(0)));
}
