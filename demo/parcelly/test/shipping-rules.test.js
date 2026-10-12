import { beforeEach, describe, expect, it } from "vitest";
import { sizeForWeight } from "../src/validation.js";
import {
  authorized,
  createPartner,
  createTestApp,
  validPallet,
  validShipment,
  withAddress,
} from "./helpers.js";

describe("shipping rules", () => {
  let client;

  beforeEach(async () => {
    const app = createTestApp();
    const partner = await createPartner(app);
    client = authorized(app, partner.api_key);
  });

  async function rejectedWith(path, body, status, code) {
    const response = await client.post(path).send(body).expect(status);
    expect(response.body.code).toBe(code);
  }

  it("limits parcels to 25000 g", async () => {
    await client
      .post("/v1/shipments")
      .send(validShipment({ parcel: { weight_g: 25000, size: "C" } }))
      .expect(201);
    await rejectedWith(
      "/v1/shipments",
      validShipment({ parcel: { weight_g: 25001, size: "C" } }),
      422,
      "WEIGHT_LIMIT_EXCEEDED",
    );
  });

  it("derives the size from the weight", () => {
    expect(sizeForWeight(1)).toBe("A");
    expect(sizeForWeight(5000)).toBe("A");
    expect(sizeForWeight(5001)).toBe("B");
    expect(sizeForWeight(15000)).toBe("B");
    expect(sizeForWeight(15001)).toBe("C");
    expect(sizeForWeight(25000)).toBe("C");
    expect(sizeForWeight(25001)).toBeNull();
  });

  it("rejects a size that does not match the weight", async () => {
    await rejectedWith(
      "/v1/shipments",
      validShipment({ parcel: { weight_g: 14500, size: "A" } }),
      422,
      "SIZE_WEIGHT_MISMATCH",
    );
    await rejectedWith(
      "/v1/shipments",
      validShipment({ parcel: { weight_g: 3000, size: "C" } }),
      422,
      "SIZE_WEIGHT_MISMATCH",
    );
  });

  it("accepts pallets between 25001 g and 1000000 g", async () => {
    await client
      .post("/v1/pallets")
      .send(validPallet({ pallet: { weight_g: 25001, pallet_type: "EUR" } }))
      .expect(201);
    await rejectedWith(
      "/v1/pallets",
      validPallet({ pallet: { weight_g: 25000, pallet_type: "EUR" } }),
      422,
      "PALLET_WEIGHT_OUT_OF_RANGE",
    );
    await rejectedWith(
      "/v1/pallets",
      validPallet({ pallet: { weight_g: 1000001, pallet_type: "EUR" } }),
      422,
      "PALLET_WEIGHT_OUT_OF_RANGE",
    );
  });

  it("requires the phone in +447XXXXXXXXX format", async () => {
    for (const phone of ["07700900200", "+44 7700 900200", "00447700900200", "+44770090020"]) {
      const shipment = validShipment();
      shipment.receiver.phone = phone;
      await rejectedWith("/v1/shipments", shipment, 422, "INVALID_PHONE");
    }
  });

  it("requires the postcode in UK format", async () => {
    await rejectedWith(
      "/v1/shipments",
      withAddress({ postcode: "e16an" }),
      422,
      "INVALID_POSTCODE",
    );
    await rejectedWith(
      "/v1/pallets",
      { ...validPallet(), receiver: withAddress({ postcode: "E16AN" }).receiver },
      422,
      "INVALID_POSTCODE",
    );
  });

  it("checks that the postcode belongs to the city", async () => {
    await rejectedWith(
      "/v1/shipments",
      withAddress({ postcode: "M1 1AE", city: "London" }),
      422,
      "POSTCODE_CITY_MISMATCH",
    );
    await rejectedWith(
      "/v1/shipments",
      withAddress({ postcode: "PO1 2AB", city: "London" }),
      422,
      "POSTCODE_CITY_MISMATCH",
    );
    await client
      .post("/v1/shipments")
      .send(withAddress({ postcode: "M1 1AE", city: "Manchester" }))
      .expect(201);
    await client
      .post("/v1/shipments")
      .send(withAddress({ postcode: "NW8 8RX", city: " london " }))
      .expect(201);
    await client
      .post("/v1/shipments")
      .send(withAddress({ postcode: "PO1 2AB", city: "Portsmouth" }))
      .expect(201);
  });

  it("applies the postcode check to pallets", async () => {
    const pallet = {
      ...validPallet(),
      receiver: withAddress({ postcode: "BS1 4DJ", city: "Cardiff" }).receiver,
    };
    await rejectedWith("/v1/pallets", pallet, 422, "POSTCODE_CITY_MISMATCH");
  });

  it("only ships within the United Kingdom", async () => {
    const response = await client
      .post("/v1/shipments")
      .send(withAddress({ country: "DE" }))
      .expect(400);
    expect(response.body.code).toBe("INVALID_REQUEST");
  });
});
