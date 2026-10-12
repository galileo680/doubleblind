import { createServer } from "node:http";
import request from "supertest";
import { createApp } from "../src/server.js";

export const FAST_RETRY_DELAYS_MS = [5, 5, 5, 5, 5];

export function createTestApp(options = {}) {
  return createApp({ sandbox: true, webhookRetryDelaysMs: FAST_RETRY_DELAYS_MS, ...options });
}

export async function createPartner(app, body = {}) {
  const response = await request(app)
    .post("/__sandbox/partners")
    .send({ name: "spokes", ...body })
    .expect(201);
  return response.body;
}

export function validShipment(overrides = {}) {
  return {
    reference: "ORD-1001",
    receiver: {
      name: "John Smith",
      phone: "+447700900200",
      address: {
        street: "Long Street",
        building: "5",
        flat: "3",
        postcode: "E1 6AN",
        city: "London",
        country: "GB",
      },
    },
    parcel: { weight_g: 14500, size: "B" },
    ...overrides,
  };
}

export function validPallet(overrides = {}) {
  const base = validShipment();
  delete base.parcel;
  return { ...base, pallet: { weight_g: 27000, pallet_type: "EUR" }, ...overrides };
}

export function withAddress(changes) {
  const shipment = validShipment();
  shipment.receiver.address = { ...shipment.receiver.address, ...changes };
  return shipment;
}

export function authorized(app, apiKey) {
  return {
    post: (path) => request(app).post(path).set("Authorization", `Bearer ${apiKey}`),
    get: (path) => request(app).get(path).set("Authorization", `Bearer ${apiKey}`),
  };
}

export async function startReceiver(statuses = []) {
  const received = [];
  const server = createServer((incoming, outgoing) => {
    const chunks = [];
    incoming.on("data", (chunk) => chunks.push(chunk));
    incoming.on("end", () => {
      received.push({
        headers: incoming.headers,
        rawBody: Buffer.concat(chunks).toString("utf8"),
      });
      outgoing.statusCode = statuses[received.length - 1] ?? 200;
      outgoing.end();
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const { port } = server.address();
  return {
    url: `http://127.0.0.1:${port}/webhooks/parcelly`,
    received,
    close: () => new Promise((resolve) => server.close(resolve)),
  };
}
