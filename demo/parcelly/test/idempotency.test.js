import request from "supertest";
import { beforeEach, describe, expect, it } from "vitest";
import { authorized, createPartner, createTestApp, validShipment } from "./helpers.js";

describe("idempotency", () => {
  let app;
  let client;

  beforeEach(async () => {
    app = createTestApp();
    const partner = await createPartner(app);
    client = authorized(app, partner.api_key);
  });

  async function charges(reference) {
    const response = await request(app)
      .get(`/__sandbox/charges?reference=${reference}`)
      .expect(200);
    return response.body;
  }

  it("returns the same shipment for a repeated Idempotency-Key and charges once", async () => {
    const first = await client
      .post("/v1/shipments")
      .set("Idempotency-Key", "ORD-1001")
      .send(validShipment())
      .expect(201);
    const second = await client
      .post("/v1/shipments")
      .set("Idempotency-Key", "ORD-1001")
      .send(validShipment())
      .expect(201);
    expect(second.body.id).toBe(first.body.id);
    expect(second.headers["idempotent-replayed"]).toBe("true");
    expect(await charges("ORD-1001")).toEqual({ reference: "ORD-1001", count: 1, total_gr: 1599 });
  });

  it("creates a new shipment and a new charge for every request without a key", async () => {
    const first = await client.post("/v1/shipments").send(validShipment()).expect(201);
    const second = await client.post("/v1/shipments").send(validShipment()).expect(201);
    expect(second.body.id).not.toBe(first.body.id);
    expect(await charges("ORD-1001")).toEqual({ reference: "ORD-1001", count: 2, total_gr: 3198 });
  });

  it("treats a blank key like a missing key", async () => {
    await client
      .post("/v1/shipments")
      .set("Idempotency-Key", " ")
      .send(validShipment())
      .expect(201);
    await client
      .post("/v1/shipments")
      .set("Idempotency-Key", " ")
      .send(validShipment())
      .expect(201);
    expect((await charges("ORD-1001")).count).toBe(2);
  });

  it("scopes keys to the partner", async () => {
    const other = await createPartner(app, { name: "other-shop" });
    const first = await client
      .post("/v1/shipments")
      .set("Idempotency-Key", "ORD-1001")
      .send(validShipment())
      .expect(201);
    const second = await authorized(app, other.api_key)
      .post("/v1/shipments")
      .set("Idempotency-Key", "ORD-1001")
      .send(validShipment())
      .expect(201);
    expect(second.body.id).not.toBe(first.body.id);
  });

  it("does not remember keys of rejected requests", async () => {
    await client
      .post("/v1/shipments")
      .set("Idempotency-Key", "ORD-1001")
      .send(validShipment({ parcel: { weight_g: 14500, size: "A" } }))
      .expect(422);
    const response = await client
      .post("/v1/shipments")
      .set("Idempotency-Key", "ORD-1001")
      .send(validShipment())
      .expect(201);
    expect(response.headers["idempotent-replayed"]).toBeUndefined();
  });

  it("rejects keys longer than 255 characters", async () => {
    const response = await client
      .post("/v1/shipments")
      .set("Idempotency-Key", "k".repeat(256))
      .send(validShipment())
      .expect(400);
    expect(response.body.code).toBe("INVALID_IDEMPOTENCY_KEY");
  });
});
