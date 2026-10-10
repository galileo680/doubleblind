import { beforeEach, describe, expect, it } from "vitest";
import { authorized, createPartner, createTestApp, validShipment } from "./helpers.js";

describe("require_idempotency_key partner flag", () => {
  let app;

  beforeEach(() => {
    app = createTestApp();
  });

  it("rejects a request without a key for a flagged partner", async () => {
    const partner = await createPartner(app, { flags: { require_idempotency_key: true } });
    const response = await authorized(app, partner.api_key)
      .post("/v1/shipments")
      .send(validShipment())
      .expect(400);
    expect(response.body.code).toBe("IDEMPOTENCY_KEY_REQUIRED");
  });

  it("accepts a request with a key for a flagged partner", async () => {
    const partner = await createPartner(app, { flags: { require_idempotency_key: true } });
    await authorized(app, partner.api_key)
      .post("/v1/shipments")
      .set("Idempotency-Key", "ORD-1001")
      .send(validShipment())
      .expect(201);
  });

  it("keeps the legacy behaviour for partners without the flag", async () => {
    const partner = await createPartner(app);
    await authorized(app, partner.api_key).post("/v1/shipments").send(validShipment()).expect(201);
  });
});
