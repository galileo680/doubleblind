import { randomBytes } from "node:crypto";
import { ApiError } from "./errors.js";

function generateApiKey() {
  return `pk_test_${randomBytes(16).toString("hex")}`;
}

export function createPartnerRegistry() {
  const partners = new Map();
  const partnersByApiKey = new Map();
  let counter = 0;

  return {
    create({ name, apiKey, flags = {} }) {
      const key = apiKey ?? generateApiKey();
      if (partnersByApiKey.has(key)) {
        throw new ApiError(409, "API_KEY_TAKEN", "A partner with this API key already exists");
      }
      counter += 1;
      const partner = {
        id: `prt_${counter}`,
        name,
        apiKey: key,
        webhook: null,
        flags: { ...flags },
      };
      partners.set(partner.id, partner);
      partnersByApiKey.set(key, partner);
      return partner;
    },

    get(id) {
      return partners.get(id) ?? null;
    },

    findByApiKey(apiKey) {
      return partnersByApiKey.get(apiKey) ?? null;
    },

    setWebhook(partner, webhook) {
      partner.webhook = { url: webhook.url, secret: webhook.secret };
    },

    reset() {
      partners.clear();
      partnersByApiKey.clear();
      counter = 0;
    },
  };
}
