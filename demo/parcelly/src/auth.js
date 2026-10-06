import { ApiError } from "./errors.js";

const BEARER_PATTERN = /^Bearer\s+(\S+)$/;

export function requirePartner(partners) {
  return (request, response, next) => {
    const match = BEARER_PATTERN.exec(request.get("authorization") ?? "");
    const partner = match ? partners.findByApiKey(match[1]) : null;
    if (partner === null) {
      response.set("WWW-Authenticate", "Bearer");
      next(new ApiError(401, "UNAUTHORIZED", "Missing or invalid partner API key"));
      return;
    }
    request.partner = partner;
    next();
  };
}
