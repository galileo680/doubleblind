export class ApiError extends Error {
  constructor(status, code, message, details) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }

  toBody() {
    const body = { code: this.code, message: this.message };
    if (this.details !== undefined) {
      body.details = this.details;
    }
    return body;
  }
}

export function notFound(message = "Resource not found") {
  return new ApiError(404, "NOT_FOUND", message);
}

export function errorHandler(error, request, response, next) {
  if (response.headersSent) {
    next(error);
    return;
  }
  if (error instanceof ApiError) {
    response.status(error.status).json(error.toBody());
    return;
  }
  if (error?.type === "entity.parse.failed") {
    response.status(400).json({ code: "INVALID_JSON", message: "Request body is not valid JSON" });
    return;
  }
  if (error?.type === "entity.too.large") {
    response.status(413).json({ code: "PAYLOAD_TOO_LARGE", message: "Request body is too large" });
    return;
  }
  console.error(error);
  response.status(500).json({ code: "INTERNAL_ERROR", message: "Unexpected server error" });
}
