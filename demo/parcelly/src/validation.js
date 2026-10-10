import Ajv from "ajv";
import { ApiError } from "./errors.js";
import { postcodeMatchesCity } from "./postcodes.js";

const MAX_PARCEL_WEIGHT_G = 25000;
const MIN_PALLET_WEIGHT_G = 25001;
const MAX_PALLET_WEIGHT_G = 1000000;
const SIZE_LIMITS_G = Object.freeze({ A: 5000, B: 15000, C: 25000 });
const ADVANCEABLE_STATUSES = Object.freeze(["COLLECTED", "IN_TRANSIT", "DELIVERED", "RETURNED"]);

const PHONE_PATTERN = /^\+48\d{9}$/;
const POSTCODE_PATTERN = /^\d{2}-\d{3}$/;

const text = (maxLength) => ({ type: "string", minLength: 1, maxLength });

const addressSchema = {
  type: "object",
  required: ["street", "building", "postcode", "city", "country"],
  additionalProperties: false,
  properties: {
    street: text(120),
    building: text(20),
    flat: text(20),
    postcode: { type: "string" },
    city: text(80),
    country: { type: "string", const: "PL" },
  },
};

const receiverSchema = {
  type: "object",
  required: ["name", "phone", "address"],
  additionalProperties: false,
  properties: {
    name: text(120),
    phone: { type: "string" },
    address: addressSchema,
  },
};

const shipmentRequestSchema = {
  type: "object",
  required: ["reference", "receiver", "parcel"],
  additionalProperties: false,
  properties: {
    reference: text(64),
    receiver: receiverSchema,
    parcel: {
      type: "object",
      required: ["weight_g", "size"],
      additionalProperties: false,
      properties: {
        weight_g: { type: "integer", minimum: 1 },
        size: { type: "string", enum: Object.keys(SIZE_LIMITS_G) },
      },
    },
  },
};

const palletRequestSchema = {
  type: "object",
  required: ["reference", "receiver", "pallet"],
  additionalProperties: false,
  properties: {
    reference: text(64),
    receiver: receiverSchema,
    pallet: {
      type: "object",
      required: ["weight_g", "pallet_type"],
      additionalProperties: false,
      properties: {
        weight_g: { type: "integer", minimum: 1 },
        pallet_type: { type: "string", const: "EUR" },
      },
    },
  },
};

const webhookRegistrationSchema = {
  type: "object",
  required: ["url", "secret"],
  additionalProperties: false,
  properties: {
    url: text(2000),
    secret: text(256),
  },
};

const sandboxPartnerSchema = {
  type: "object",
  required: ["name"],
  additionalProperties: false,
  properties: {
    name: text(120),
    api_key: text(256),
    flags: { type: "object", additionalProperties: { type: "boolean" } },
  },
};

const advanceSchema = {
  type: "object",
  required: ["to"],
  additionalProperties: false,
  properties: {
    to: { type: "string", enum: ADVANCEABLE_STATUSES },
  },
};

const ajv = new Ajv({ allErrors: true });
const validators = {
  shipment: ajv.compile(shipmentRequestSchema),
  pallet: ajv.compile(palletRequestSchema),
  webhook: ajv.compile(webhookRegistrationSchema),
  sandboxPartner: ajv.compile(sandboxPartnerSchema),
  advance: ajv.compile(advanceSchema),
};

function describe(error) {
  const path = error.instancePath || "/";
  const extra = error.params?.additionalProperty;
  return extra
    ? `${path} must not have additional property '${extra}'`
    : `${path} ${error.message}`;
}

function assertSchema(validator, body) {
  if (!validator(body)) {
    throw new ApiError(400, "INVALID_REQUEST", describe(validator.errors[0]));
  }
}

function assertReceiverFormats(receiver) {
  if (!PHONE_PATTERN.test(receiver.phone)) {
    throw new ApiError(422, "INVALID_PHONE", "Receiver phone must have the format +48XXXXXXXXX");
  }
  if (!POSTCODE_PATTERN.test(receiver.address.postcode)) {
    throw new ApiError(422, "INVALID_POSTCODE", "Postcode must have the format NN-NNN");
  }
}

function assertPostcodeMatchesCity(address) {
  if (!postcodeMatchesCity(address.postcode, address.city)) {
    throw new ApiError(
      422,
      "POSTCODE_CITY_MISMATCH",
      `Postcode ${address.postcode} does not belong to ${address.city}`,
    );
  }
}

export function sizeForWeight(weightG) {
  const match = Object.entries(SIZE_LIMITS_G).find(([, limit]) => weightG <= limit);
  return match ? match[0] : null;
}

export function validateShipmentRequest(body) {
  assertSchema(validators.shipment, body);
  assertReceiverFormats(body.receiver);
  const { weight_g: weightG, size } = body.parcel;
  if (weightG > MAX_PARCEL_WEIGHT_G) {
    throw new ApiError(
      422,
      "WEIGHT_LIMIT_EXCEEDED",
      `Parcel weight ${weightG} g exceeds the limit of ${MAX_PARCEL_WEIGHT_G} g`,
    );
  }
  if (sizeForWeight(weightG) !== size) {
    throw new ApiError(
      422,
      "SIZE_WEIGHT_MISMATCH",
      `Size ${size} does not match parcel weight ${weightG} g`,
    );
  }
  assertPostcodeMatchesCity(body.receiver.address);
}

export function validatePalletRequest(body) {
  assertSchema(validators.pallet, body);
  assertReceiverFormats(body.receiver);
  const weightG = body.pallet.weight_g;
  if (weightG < MIN_PALLET_WEIGHT_G || weightG > MAX_PALLET_WEIGHT_G) {
    throw new ApiError(
      422,
      "PALLET_WEIGHT_OUT_OF_RANGE",
      `Pallet weight must be between ${MIN_PALLET_WEIGHT_G} g and ${MAX_PALLET_WEIGHT_G} g`,
    );
  }
  assertPostcodeMatchesCity(body.receiver.address);
}

export function validateWebhookRegistration(body) {
  assertSchema(validators.webhook, body);
  let url;
  try {
    url = new URL(body.url);
  } catch {
    url = null;
  }
  if (url === null || !["http:", "https:"].includes(url.protocol)) {
    throw new ApiError(400, "INVALID_WEBHOOK_URL", "Webhook url must be an absolute http(s) URL");
  }
}

export function validateSandboxPartner(body) {
  assertSchema(validators.sandboxPartner, body);
}

export function validateAdvance(body) {
  assertSchema(validators.advance, body);
}
