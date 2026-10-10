import express from "express";
import { ApiError } from "./errors.js";
import { validatePalletRequest, validateShipmentRequest } from "./validation.js";
import { STATUS_CHANGED_EVENT } from "./webhooks.js";

export const PARCEL_PRICES_GR = Object.freeze({ A: 1299, B: 1599, C: 1999 });
export const PALLET_PRICE_GR = 8900;

const SERVICES = Object.freeze({
  parcel: {
    idPrefix: "shp",
    trackingPrefix: "PCL",
    validate: validateShipmentRequest,
    price: (body) => PARCEL_PRICES_GR[body.parcel.size],
    load: (body) => ({ parcel: { ...body.parcel } }),
  },
  pallet: {
    idPrefix: "plt",
    trackingPrefix: "PAL",
    validate: validatePalletRequest,
    price: () => PALLET_PRICE_GR,
    load: (body) => ({ pallet: { ...body.pallet } }),
  },
});

export function serializeShipment(shipment) {
  return {
    id: shipment.id,
    service: shipment.service,
    reference: shipment.reference,
    status: shipment.status,
    tracking_number: shipment.trackingNumber,
    price_gr: shipment.priceGr,
    receiver: structuredClone(shipment.receiver),
    ...structuredClone(shipment.load),
    created_at: shipment.createdAt,
    updated_at: shipment.updatedAt,
  };
}

function shipmentNotFound() {
  return new ApiError(404, "SHIPMENT_NOT_FOUND", "Shipment not found");
}

export function createShipmentService({ partners, webhooks, clock = () => new Date() }) {
  const shipments = new Map();
  const idempotencyKeys = new Map();
  const charges = [];
  const counters = { parcel: 0, pallet: 0 };
  let sequence = 0;

  function latest(candidates) {
    return candidates.reduce(
      (newest, shipment) => (newest === null || shipment.seq > newest.seq ? shipment : newest),
      null,
    );
  }

  return {
    create(partner, serviceName, body, idempotencyKey) {
      if (!idempotencyKey && partner.flags.require_idempotency_key) {
        throw new ApiError(
          400,
          "IDEMPOTENCY_KEY_REQUIRED",
          "This partner must send an Idempotency-Key header",
        );
      }
      const scopedKey = idempotencyKey ? `${partner.id}:${idempotencyKey}` : null;
      if (scopedKey !== null && idempotencyKeys.has(scopedKey)) {
        return { shipment: shipments.get(idempotencyKeys.get(scopedKey)), replayed: true };
      }
      const service = SERVICES[serviceName];
      service.validate(body);
      counters[serviceName] += 1;
      sequence += 1;
      const number = counters[serviceName];
      const now = clock().toISOString();
      const shipment = {
        seq: sequence,
        id: `${service.idPrefix}_${number}`,
        service: serviceName,
        partnerId: partner.id,
        reference: body.reference,
        status: "CREATED",
        trackingNumber: `${service.trackingPrefix}${number}PL`,
        priceGr: service.price(body),
        receiver: structuredClone(body.receiver),
        load: service.load(body),
        createdAt: now,
        updatedAt: now,
      };
      shipments.set(shipment.id, shipment);
      charges.push({
        shipmentId: shipment.id,
        partnerId: partner.id,
        reference: shipment.reference,
        amountGr: shipment.priceGr,
        chargedAt: now,
      });
      if (scopedKey !== null) {
        idempotencyKeys.set(scopedKey, shipment.id);
      }
      return { shipment, replayed: false };
    },

    getForPartner(partner, id) {
      const shipment = shipments.get(id);
      return shipment && shipment.partnerId === partner.id ? shipment : null;
    },

    findLatestByReference(reference, partner = null) {
      const candidates = [...shipments.values()].filter(
        (shipment) =>
          shipment.reference === reference &&
          (partner === null || shipment.partnerId === partner.id),
      );
      return latest(candidates);
    },

    get(id) {
      return shipments.get(id) ?? null;
    },

    advance(shipment, status) {
      if (shipment.status === status) {
        return shipment;
      }
      shipment.status = status;
      shipment.updatedAt = clock().toISOString();
      const webhook = partners.get(shipment.partnerId)?.webhook;
      if (webhook) {
        webhooks.dispatch(webhook, {
          event: STATUS_CHANGED_EVENT,
          shipment_id: shipment.id,
          reference: shipment.reference,
          status: shipment.status,
          occurred_at: shipment.updatedAt,
        });
      }
      return shipment;
    },

    chargesFor(reference) {
      const matching = charges.filter((charge) => charge.reference === reference);
      return {
        reference,
        count: matching.length,
        total_gr: matching.reduce((total, charge) => total + charge.amountGr, 0),
      };
    },

    reset() {
      shipments.clear();
      idempotencyKeys.clear();
      charges.length = 0;
      counters.parcel = 0;
      counters.pallet = 0;
      sequence = 0;
    },
  };
}

function idempotencyKeyFrom(request) {
  const value = request.get("idempotency-key");
  if (value === undefined) {
    return null;
  }
  const trimmed = value.trim();
  if (trimmed.length > 255) {
    throw new ApiError(
      400,
      "INVALID_IDEMPOTENCY_KEY",
      "Idempotency-Key must be at most 255 characters",
    );
  }
  return trimmed === "" ? null : trimmed;
}

export function createShipmentsRouter({ shipments }) {
  const router = express.Router();

  const create = (serviceName) => (request, response) => {
    const { shipment, replayed } = shipments.create(
      request.partner,
      serviceName,
      request.body,
      idempotencyKeyFrom(request),
    );
    if (replayed) {
      response.set("Idempotent-Replayed", "true");
    }
    response.status(201).json(serializeShipment(shipment));
  };

  router.post("/shipments", create("parcel"));
  router.post("/pallets", create("pallet"));

  router.get("/shipments", (request, response) => {
    const reference = request.query.reference;
    if (typeof reference !== "string" || reference === "") {
      throw new ApiError(400, "REFERENCE_REQUIRED", "Query parameter 'reference' is required");
    }
    const shipment = shipments.findLatestByReference(reference, request.partner);
    if (shipment === null) {
      throw shipmentNotFound();
    }
    response.json(serializeShipment(shipment));
  });

  router.get("/shipments/:id", (request, response) => {
    const shipment = shipments.getForPartner(request.partner, request.params.id);
    if (shipment === null) {
      throw shipmentNotFound();
    }
    response.json(serializeShipment(shipment));
  });

  return router;
}
