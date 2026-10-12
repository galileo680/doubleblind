import request from "supertest";
import { beforeEach, describe, expect, it } from "vitest";
import { authorized, createPartner, createTestApp, validPallet, validShipment } from "./helpers.js";

describe("shipments API", () => {
  let app;
  let client;

  beforeEach(async () => {
    app = createTestApp();
    const partner = await createPartner(app);
    client = authorized(app, partner.api_key);
  });

  it("reports health", async () => {
    const response = await request(app).get("/health").expect(200);
    expect(response.body.status).toBe("ok");
  });

  it("creates a parcel shipment with identifiers and price", async () => {
    const response = await client.post("/v1/shipments").send(validShipment()).expect(201);
    expect(response.body).toMatchObject({
      id: "shp_1",
      service: "parcel",
      reference: "ORD-1001",
      status: "CREATED",
      tracking_number: "PCL1GB",
      price_pence: 1599,
      parcel: { weight_g: 14500, size: "B" },
    });
  });

  it("prices parcels by size", async () => {
    const small = validShipment({ reference: "ORD-A", parcel: { weight_g: 800, size: "A" } });
    const large = validShipment({ reference: "ORD-C", parcel: { weight_g: 20000, size: "C" } });
    expect((await client.post("/v1/shipments").send(small).expect(201)).body.price_pence).toBe(
      1299,
    );
    expect((await client.post("/v1/shipments").send(large).expect(201)).body.price_pence).toBe(
      1999,
    );
  });

  it("creates a pallet shipment with its own numbering", async () => {
    await client.post("/v1/shipments").send(validShipment()).expect(201);
    const response = await client.post("/v1/pallets").send(validPallet()).expect(201);
    expect(response.body).toMatchObject({
      id: "plt_1",
      service: "pallet",
      tracking_number: "PAL1GB",
      price_pence: 8900,
      pallet: { weight_g: 27000, pallet_type: "EUR" },
    });
  });

  it("returns a shipment by id", async () => {
    await client.post("/v1/shipments").send(validShipment()).expect(201);
    const response = await client.get("/v1/shipments/shp_1").expect(200);
    expect(response.body.reference).toBe("ORD-1001");
  });

  it("finds the newest shipment for a reference", async () => {
    await client.post("/v1/shipments").send(validShipment()).expect(201);
    await client.post("/v1/shipments").send(validShipment()).expect(201);
    const response = await client.get("/v1/shipments?reference=ORD-1001").expect(200);
    expect(response.body.id).toBe("shp_2");
  });

  it("requires a reference when searching", async () => {
    const response = await client.get("/v1/shipments").expect(400);
    expect(response.body.code).toBe("REFERENCE_REQUIRED");
  });

  it("returns 404 for unknown shipments", async () => {
    const byId = await client.get("/v1/shipments/shp_404").expect(404);
    const byReference = await client.get("/v1/shipments?reference=missing").expect(404);
    expect(byId.body.code).toBe("SHIPMENT_NOT_FOUND");
    expect(byReference.body.code).toBe("SHIPMENT_NOT_FOUND");
  });

  it("hides shipments of other partners", async () => {
    await client.post("/v1/shipments").send(validShipment()).expect(201);
    const other = await createPartner(app, { name: "other-shop" });
    const otherClient = authorized(app, other.api_key);
    await otherClient.get("/v1/shipments/shp_1").expect(404);
    await otherClient.get("/v1/shipments?reference=ORD-1001").expect(404);
  });

  it("rejects requests without a valid API key", async () => {
    const missing = await request(app).post("/v1/shipments").send(validShipment()).expect(401);
    expect(missing.body.code).toBe("UNAUTHORIZED");
    await authorized(app, "pk_unknown").post("/v1/shipments").send(validShipment()).expect(401);
  });

  it("rejects bodies that do not match the schema", async () => {
    const withoutReceiver = validShipment();
    delete withoutReceiver.receiver;
    const missing = await client.post("/v1/shipments").send(withoutReceiver).expect(400);
    expect(missing.body.code).toBe("INVALID_REQUEST");

    const extra = await client
      .post("/v1/shipments")
      .send({ ...validShipment(), note: "fragile" })
      .expect(400);
    expect(extra.body.message).toContain("note");

    const fractional = validShipment({ parcel: { weight_g: 14.5, size: "B" } });
    await client.post("/v1/shipments").send(fractional).expect(400);
  });

  it("rejects malformed JSON", async () => {
    const response = await client
      .post("/v1/shipments")
      .set("Content-Type", "application/json")
      .send("{not json")
      .expect(400);
    expect(response.body.code).toBe("INVALID_JSON");
  });

  it("returns 404 for unknown routes", async () => {
    const response = await request(app).get("/nowhere").expect(404);
    expect(response.body.code).toBe("NOT_FOUND");
  });
});
