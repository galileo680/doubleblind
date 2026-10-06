import express from "express";
import { ApiError } from "./errors.js";
import { serializeShipment } from "./shipments.js";
import { validateAdvance, validateSandboxPartner } from "./validation.js";

export function createSandboxRouter({ partners, shipments, webhooks }) {
  const router = express.Router();

  router.post("/reset", (request, response) => {
    webhooks.reset();
    shipments.reset();
    partners.reset();
    response.json({ status: "reset" });
  });

  router.post("/partners", (request, response) => {
    validateSandboxPartner(request.body);
    const partner = partners.create({
      name: request.body.name,
      apiKey: request.body.api_key,
      flags: request.body.flags,
    });
    response.status(201).json({ partner_id: partner.id, api_key: partner.apiKey });
  });

  const advance = (findShipment) => (request, response) => {
    validateAdvance(request.body);
    const shipment = findShipment(request.params);
    if (shipment === null) {
      throw new ApiError(404, "SHIPMENT_NOT_FOUND", "Shipment not found");
    }
    response.json(serializeShipment(shipments.advance(shipment, request.body.to)));
  };

  router.post(
    "/shipments/by-reference/:reference/advance",
    advance(({ reference }) => shipments.findLatestByReference(reference)),
  );
  router.post(
    "/shipments/:id/advance",
    advance(({ id }) => shipments.get(id)),
  );

  router.get("/charges", (request, response) => {
    const reference = request.query.reference;
    if (typeof reference !== "string" || reference === "") {
      throw new ApiError(400, "REFERENCE_REQUIRED", "Query parameter 'reference' is required");
    }
    response.json(shipments.chargesFor(reference));
  });

  return router;
}
