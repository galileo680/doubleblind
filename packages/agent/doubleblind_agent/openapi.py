from typing import Any

SPEC_FILES = ("openapi.yaml", "openapi.yml", "openapi.json")
SCHEMA_REF = "#/components/schemas/"
METHODS = ("get", "post", "put", "patch", "delete")
IDEMPOTENCY_HEADER = "Idempotency-Key"


def contract_refs(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: item.replace(SCHEMA_REF, "#/schemas/")
            if key == "$ref" and isinstance(item, str)
            else contract_refs(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [contract_refs(item) for item in value]
    return value


def resolved(spec: dict[str, Any], item: Any) -> Any:
    while isinstance(item, dict) and str(item.get("$ref", "")).startswith("#/components/"):
        if item["$ref"].startswith(SCHEMA_REF):
            return item
        node: Any = spec
        for part in item["$ref"].removeprefix("#/").split("/"):
            node = node[part]
        item = node
    return item


def json_content(spec: dict[str, Any], holder: Any) -> dict[str, Any]:
    content = (resolved(spec, holder) or {}).get("content") or {}
    found: dict[str, Any] = content.get("application/json") or {}
    return found


def header_rules(
    spec: dict[str, Any], parameters: list[Any] | None, secured: bool
) -> dict[str, Any]:
    rules: dict[str, Any] = (
        {"Authorization": {"required": True, "pattern": "^Bearer .+$"}} if secured else {}
    )
    for parameter in map(lambda item: resolved(spec, item), parameters or []):
        if parameter.get("in") != "header":
            continue
        rule: dict[str, Any] = {"required": bool(parameter.get("required"))}
        pattern = (parameter.get("schema") or {}).get("pattern")
        if pattern:
            rule["pattern"] = pattern
        rules[parameter["name"]] = rule
    return rules


def operation(spec: dict[str, Any], path: str, method: str, item: dict[str, Any]) -> dict[str, Any]:
    secured = bool(item.get("security", spec.get("security")))
    headers = header_rules(spec, item.get("parameters"), secured)
    request: dict[str, Any] = {"method": method.upper(), "path": path, "headers": headers}
    body = json_content(spec, item.get("requestBody")).get("schema")
    if body:
        request["body"] = contract_refs(body)
    responses = {}
    for status, response in item.get("responses", {}).items():
        schema = json_content(spec, response).get("schema")
        responses[str(status)] = {"body": contract_refs(schema)} if schema else {}
    success = next(status for status in responses if status.startswith("2"))
    stub: dict[str, Any] = {
        "default_status": int(success),
        "body": json_content(spec, item["responses"][success]).get("example"),
    }
    if IDEMPOTENCY_HEADER in headers:
        stub["idempotent_on_header"] = IDEMPOTENCY_HEADER
    return {
        "description": item.get("summary"),
        "direction": "consumer_to_provider",
        "request": request,
        "responses": responses,
        "stub": stub,
    }


def webhook(spec: dict[str, Any], method: str, item: dict[str, Any]) -> dict[str, Any]:
    request: dict[str, Any] = {
        "method": method.upper(),
        "url": "${CONSUMER_WEBHOOK_URL}",
        "headers": header_rules(spec, item.get("parameters"), False),
    }
    body = json_content(spec, item.get("requestBody")).get("schema")
    if body:
        request["body"] = contract_refs(body)
    found = {"description": item.get("summary"), "direction": "provider_to_consumer"}
    found["request"] = request
    if item.get("x-signature"):
        found["signature"] = item["x-signature"]
    return found


def contract_sections(spec: dict[str, Any]) -> dict[str, Any]:
    operations = {
        item["operationId"]: operation(spec, path, method, item)
        for path, methods in spec.get("paths", {}).items()
        for method, item in methods.items()
        if method in METHODS
    }
    webhooks = {
        name: webhook(spec, method, item)
        for name, methods in spec.get("webhooks", {}).items()
        for method, item in methods.items()
        if method in METHODS
    }
    secrets = {
        name: scheme.get("description") or name
        for name, scheme in spec.get("components", {}).get("securitySchemes", {}).items()
    }
    for found in webhooks.values():
        if "signature" in found:
            secrets.setdefault(found["signature"]["secret"], "Secret for webhook signatures")
    return {
        "schemas": contract_refs(spec.get("components", {}).get("schemas", {})),
        "operations": operations,
        "webhooks": webhooks,
        "secrets": [{"name": name, "description": text} for name, text in secrets.items()],
    }
