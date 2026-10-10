export class ApiError extends Error {
  constructor(status, code, message) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

export function errorHandler(error, request, response, next) {
  if (response.headersSent) {
    next(error);
    return;
  }
  if (error instanceof ApiError) {
    response.status(error.status).json({ code: error.code, message: error.message });
    return;
  }
  if (error?.type === "entity.parse.failed") {
    response.status(400).json({ code: "INVALID_JSON", message: "Request body is not valid JSON" });
    return;
  }
  console.error(error);
  response.status(500).json({ code: "INTERNAL_ERROR", message: "Unexpected server error" });
}
