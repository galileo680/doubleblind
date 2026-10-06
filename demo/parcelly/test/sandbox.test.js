import request from "supertest";
import { beforeEach, describe, expect, it } from "vitest";
import { createApp } from "../src/server.js";
import { authorized, createPartner, createTestApp, validShipment } from "./helpers.js";

describe("sandbox endpoints", () => {
  let app;

  beforeEach(() => {
    app = createTestApp();
  });

  it("are not available outside sandbox mode", async () => {
    const production = createApp({ sandbox: false });
    await request(production).post("/__sandbox/reset").expect(404);
    await request(production).post("/__sandbox/partners").send({ name: "spokes" }).expect(404);
  });

  it("create partners with a generated API key", async () => {
    const partner = await createPartner(app);
    expect(partner.partner_id).toBe("prt_1");
    expect(partner.api_key).toMatch(/^pk_test_[0-9a-f]{32}$/);
  });

  it("create partners with a given API key", async () => {
    const partner = await createPartner(app, { api_key: "shared-secret-key" });
    expect(partner.api_key).toBe("shared-secret-key");
    await authorized(app, "shared-secret-key")
      .post("/v1/shipments")
      .send(validShipment())
      .expect(201);
  });

  it("reject a duplicate API key", async () => {
    await createPartner(app, { api_key: "shared-secret-key" });
    const response = await request(app)
      .post("/__sandbox/partners")
      .send({ name: "copy", api_key: "shared-secret-key" })
      .expect(409);
    expect(response.body.code).toBe("API_KEY_TAKEN");
  });

  it("accept partner flags", async () => {
    await createPartner(app, { flags: { beta_pricing: true } });
    const response = await request(app)
      .post("/__sandbox/partners")
      .send({ name: "broken", flags: { beta_pricing: "yes" } })
      .expect(400);
    expect(response.body.code).toBe("INVALID_REQUEST");
  });

  it("reset all data and counters", async () => {
    const partner = await createPartner(app);
    await authorized(app, partner.api_key).post("/v1/shipments").send(validShipment()).expect(201);
    await request(app).post("/__sandbox/reset").expect(200);
    await authorized(app, partner.api_key).post("/v1/shipments").send(validShipment()).expect(401);
    const fresh = await createPartner(app);
    expect(fresh.partner_id).toBe("prt_1");
    const response = await authorized(app, fresh.api_key)
      .post("/v1/shipments")
      .send(validShipment())
      .expect(201);
    expect(response.body.id).toBe("shp_1");
  });

  it("advance a shipment by id and by reference", async () => {
    const partner = await createPartner(app);
    const client = authorized(app, partner.api_key);
    await client.post("/v1/shipments").send(validShipment()).expect(201);
    const byId = await request(app)
      .post("/__sandbox/shipments/shp_1/advance")
      .send({ to: "COLLECTED" })
      .expect(200);
    expect(byId.body.status).toBe("COLLECTED");
    const byReference = await request(app)
      .post("/__sandbox/shipments/by-reference/ORD-1001/advance")
      .send({ to: "DELIVERED" })
      .expect(200);
    expect(byReference.body).toMatchObject({ id: "shp_1", status: "DELIVERED" });
    const stored = await client.get("/v1/shipments/shp_1").expect(200);
    expect(stored.body.status).toBe("DELIVERED");
  });

  it("reject unknown statuses and shipments", async () => {
    const partner = await createPartner(app);
    await authorized(app, partner.api_key).post("/v1/shipments").send(validShipment()).expect(201);
    const invalid = await request(app)
      .post("/__sandbox/shipments/shp_1/advance")
      .send({ to: "LOST" })
      .expect(400);
    expect(invalid.body.code).toBe("INVALID_REQUEST");
    const missing = await request(app)
      .post("/__sandbox/shipments/by-reference/ORD-404/advance")
      .send({ to: "DELIVERED" })
      .expect(404);
    expect(missing.body.code).toBe("SHIPMENT_NOT_FOUND");
  });

  it("report charges for a reference", async () => {
    const empty = await request(app).get("/__sandbox/charges?reference=ORD-1").expect(200);
    expect(empty.body).toEqual({ reference: "ORD-1", count: 0, total_gr: 0 });
    await request(app).get("/__sandbox/charges").expect(400);
  });
});
