import { createHmac } from "node:crypto";
import request from "supertest";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import {
  FAST_RETRY_DELAYS_MS,
  authorized,
  createPartner,
  createTestApp,
  startReceiver,
  validShipment,
} from "./helpers.js";

const SECRET = "whsec_test_secret";

describe("webhooks", () => {
  let app;
  let client;
  let receiver;

  beforeEach(async () => {
    app = createTestApp();
    const partner = await createPartner(app);
    client = authorized(app, partner.api_key);
  });

  afterEach(async () => {
    await app.locals.parcelly.webhooks.idle();
    await receiver?.close();
    receiver = undefined;
  });

  async function register(statuses) {
    receiver = await startReceiver(statuses);
    await client.post("/v1/webhooks").send({ url: receiver.url, secret: SECRET }).expect(201);
  }

  async function createAndAdvance(to) {
    await client
      .post("/v1/shipments")
      .set("Idempotency-Key", "ORD-1001")
      .send(validShipment())
      .expect(201);
    await request(app).post("/__sandbox/shipments/shp_1/advance").send({ to }).expect(200);
    await app.locals.parcelly.webhooks.idle();
  }

  it("registers a webhook endpoint", async () => {
    receiver = await startReceiver();
    const response = await client
      .post("/v1/webhooks")
      .send({ url: receiver.url, secret: SECRET })
      .expect(201);
    expect(response.body).toEqual({ url: receiver.url, events: ["shipment.status_changed"] });
    const current = await client.get("/v1/webhooks").expect(200);
    expect(current.body.url).toBe(receiver.url);
  });

  it("rejects invalid registrations", async () => {
    const relative = await client
      .post("/v1/webhooks")
      .send({ url: "/hook", secret: SECRET })
      .expect(400);
    expect(relative.body.code).toBe("INVALID_WEBHOOK_URL");
    const ftp = await client
      .post("/v1/webhooks")
      .send({ url: "ftp://example.com", secret: SECRET })
      .expect(400);
    expect(ftp.body.code).toBe("INVALID_WEBHOOK_URL");
    const noSecret = await client
      .post("/v1/webhooks")
      .send({ url: "https://example.com" })
      .expect(400);
    expect(noSecret.body.code).toBe("INVALID_REQUEST");
  });

  it("sends a signed status change event", async () => {
    await register();
    await createAndAdvance("DELIVERED");
    expect(receiver.received).toHaveLength(1);
    const [delivery] = receiver.received;
    const expected = `sha256=${createHmac("sha256", SECRET).update(delivery.rawBody).digest("hex")}`;
    expect(delivery.headers["x-parcelly-signature"]).toBe(expected);
    expect(delivery.headers["content-type"]).toBe("application/json");
    const event = JSON.parse(delivery.rawBody);
    expect(event).toMatchObject({
      event: "shipment.status_changed",
      shipment_id: "shp_1",
      reference: "ORD-1001",
      status: "DELIVERED",
    });
    expect(Number.isNaN(Date.parse(event.occurred_at))).toBe(false);
  });

  it("does not send an event when a shipment is created", async () => {
    await register();
    await client.post("/v1/shipments").send(validShipment()).expect(201);
    await app.locals.parcelly.webhooks.idle();
    expect(receiver.received).toHaveLength(0);
  });

  it("does not send an event when the status does not change", async () => {
    await register();
    await createAndAdvance("IN_TRANSIT");
    await request(app)
      .post("/__sandbox/shipments/shp_1/advance")
      .send({ to: "IN_TRANSIT" })
      .expect(200);
    await app.locals.parcelly.webhooks.idle();
    expect(receiver.received).toHaveLength(1);
  });

  it("retries until the endpoint answers with 2xx", async () => {
    await register([500, 503, 200]);
    await createAndAdvance("COLLECTED");
    expect(receiver.received).toHaveLength(3);
    const bodies = new Set(receiver.received.map((delivery) => delivery.rawBody));
    expect(bodies.size).toBe(1);
  });

  it("gives up after five retries", async () => {
    await register(Array(10).fill(401));
    await createAndAdvance("RETURNED");
    expect(receiver.received).toHaveLength(FAST_RETRY_DELAYS_MS.length + 1);
    const [record] = app.locals.parcelly.webhooks.deliveries();
    expect(record.delivered).toBe(false);
  });

  it("skips delivery when no webhook is registered", async () => {
    await createAndAdvance("DELIVERED");
    expect(app.locals.parcelly.webhooks.deliveries()).toHaveLength(0);
  });
});
