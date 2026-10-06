import { createHmac } from "node:crypto";
import express from "express";
import { validateWebhookRegistration } from "./validation.js";

export const STATUS_CHANGED_EVENT = "shipment.status_changed";
export const SIGNATURE_HEADER = "X-Parcelly-Signature";
export const DEFAULT_RETRY_DELAYS_MS = Object.freeze([500, 1000, 2000, 4000, 8000]);
const ATTEMPT_TIMEOUT_MS = 5000;

export function signPayload(secret, rawBody) {
  return `sha256=${createHmac("sha256", secret).update(rawBody).digest("hex")}`;
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

export function createWebhookDispatcher({
  retryDelaysMs = DEFAULT_RETRY_DELAYS_MS,
  fetchImpl = fetch,
  log = () => {},
} = {}) {
  const pending = new Set();
  const deliveries = [];
  let generation = 0;

  async function attempt(webhook, rawBody, signature) {
    try {
      const response = await fetchImpl(webhook.url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "User-Agent": "Parcelly-Webhooks/1.0",
          [SIGNATURE_HEADER]: signature,
        },
        body: rawBody,
        signal: AbortSignal.timeout(ATTEMPT_TIMEOUT_MS),
      });
      return { ok: response.ok, status: response.status };
    } catch (error) {
      return { ok: false, status: null, error: error.message };
    }
  }

  async function deliver(webhook, event, startedIn) {
    const rawBody = JSON.stringify(event);
    const signature = signPayload(webhook.secret, rawBody);
    const record = { url: webhook.url, event, attempts: [], delivered: false };
    deliveries.push(record);
    for (let index = 0; index <= retryDelaysMs.length; index += 1) {
      if (index > 0) {
        await sleep(retryDelaysMs[index - 1]);
      }
      if (generation !== startedIn) {
        return record;
      }
      const result = await attempt(webhook, rawBody, signature);
      record.attempts.push(result);
      if (result.ok) {
        record.delivered = true;
        return record;
      }
    }
    log(`webhook ${event.event} for ${event.shipment_id} not delivered to ${webhook.url}`);
    return record;
  }

  return {
    dispatch(webhook, event) {
      const task = deliver(webhook, event, generation).finally(() => pending.delete(task));
      pending.add(task);
      return task;
    },

    async idle() {
      while (pending.size > 0) {
        await Promise.allSettled([...pending]);
      }
    },

    deliveries() {
      return deliveries.slice();
    },

    reset() {
      generation += 1;
      deliveries.length = 0;
    },
  };
}

export function createWebhooksRouter({ partners }) {
  const router = express.Router();

  router.post("/webhooks", (request, response) => {
    validateWebhookRegistration(request.body);
    partners.setWebhook(request.partner, request.body);
    response.status(201).json({ url: request.body.url, events: [STATUS_CHANGED_EVENT] });
  });

  router.get("/webhooks", (request, response) => {
    const webhook = request.partner.webhook;
    response.json(webhook ? { url: webhook.url, events: [STATUS_CHANGED_EVENT] } : null);
  });

  return router;
}
