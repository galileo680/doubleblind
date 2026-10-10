import { createHmac } from "node:crypto";
import express from "express";
import { validateWebhookRegistration } from "./validation.js";

export const STATUS_CHANGED_EVENT = "shipment.status_changed";
const SIGNATURE_HEADER = "X-Parcelly-Signature";
const DEFAULT_RETRY_DELAYS_MS = Object.freeze([500, 1000, 2000, 4000, 8000]);
const ATTEMPT_TIMEOUT_MS = 5000;

function signPayload(secret, rawBody) {
  return `sha256=${createHmac("sha256", secret).update(rawBody).digest("hex")}`;
}

function sleep(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

export function createWebhookDispatcher({
  retryDelaysMs = DEFAULT_RETRY_DELAYS_MS,
  log = () => {},
} = {}) {
  const pending = new Set();
  let generation = 0;

  async function attempt(webhook, rawBody, signature) {
    try {
      const response = await fetch(webhook.url, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "User-Agent": "Parcelly-Webhooks/1.0",
          [SIGNATURE_HEADER]: signature,
        },
        body: rawBody,
        signal: AbortSignal.timeout(ATTEMPT_TIMEOUT_MS),
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  async function deliver(webhook, event, startedIn) {
    const rawBody = JSON.stringify(event);
    const signature = signPayload(webhook.secret, rawBody);
    for (let index = 0; index <= retryDelaysMs.length; index += 1) {
      if (index > 0) {
        await sleep(retryDelaysMs[index - 1]);
      }
      if (generation !== startedIn) {
        return;
      }
      if (await attempt(webhook, rawBody, signature)) {
        return;
      }
    }
    log(`webhook ${event.event} for ${event.shipment_id} not delivered to ${webhook.url}`);
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

    reset() {
      generation += 1;
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
