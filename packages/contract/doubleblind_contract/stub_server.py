import json
import re
import threading
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

import httpx
from jsonschema import Draft202012Validator

from doubleblind_contract.http_server import (
    BackgroundServer,
    IncomingRequest,
    OutgoingResponse,
    header_value,
)
from doubleblind_contract.loader import format_path
from doubleblind_contract.models import Contract, EmitSignature, HeaderRule, Operation
from doubleblind_contract.runtime import ConfigurationError
from doubleblind_contract.signing import sign
from doubleblind_contract.templating import TemplateError, render

CONTROL_PREFIX = "/__stub/"
INVALID_SIGNATURE_SUFFIX = "-invalid"


@dataclass
class ReceivedRequest:
    operation: str | None
    method: str
    path: str
    headers: dict[str, str]
    body: Any
    status: int
    replayed: bool = False
    violations: list[str] = field(default_factory=list)


def path_pattern(template: str) -> re.Pattern[str]:
    parts = re.split(r"(\{[A-Za-z_][A-Za-z0-9_]*\})", template)
    regex = "".join(
        f"(?P<{part[1:-1]}>[^/]+)" if part.startswith("{") else re.escape(part) for part in parts
    )
    return re.compile(f"^{regex}$")


def header_violations(rules: Mapping[str, HeaderRule], headers: Mapping[str, str]) -> list[str]:
    violations: list[str] = []
    for name, rule in rules.items():
        value = header_value(headers, name)
        if value is None:
            if rule.required:
                violations.append(f"missing required header {name}")
            continue
        if rule.pattern is not None and re.search(rule.pattern, value) is None:
            violations.append(f"header {name} does not match /{rule.pattern}/")
    return violations


def body_violations(schema: Any, body: Any) -> list[str]:
    if schema is None:
        return []
    return [
        f"{format_path(['body', *error.absolute_path])}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(body)
    ]


class ProviderStub:
    def __init__(
        self,
        contract: Contract,
        secrets: Mapping[str, str],
        webhook_receiver_url: str | None = None,
        port: int = 0,
    ) -> None:
        self.contract = contract
        self.secrets = dict(secrets)
        self.webhook_receiver_url = webhook_receiver_url
        self.routes = [
            (operation_id, operation, path_pattern(operation.request.path))
            for operation_id, operation in contract.operations.items()
        ]
        self.lock = threading.Lock()
        self.entries: list[ReceivedRequest] = []
        self.sequences: dict[str, int] = {}
        self.remembered: dict[tuple[str, str], tuple[int, Any]] = {}
        self.server = BackgroundServer(self.handle, port=port)

    @property
    def url(self) -> str:
        return self.server.url

    def start(self) -> None:
        self.server.start()

    def stop(self) -> None:
        self.server.stop()

    def __enter__(self) -> "ProviderStub":
        self.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.stop()

    def reset(self) -> None:
        with self.lock:
            self.entries.clear()
            self.sequences.clear()
            self.remembered.clear()

    def log(self) -> list[ReceivedRequest]:
        with self.lock:
            return list(self.entries)

    def find_route(self, method: str, path: str) -> tuple[str, Operation] | None:
        for operation_id, operation, pattern in self.routes:
            if operation.request.method == method and pattern.match(path):
                return operation_id, operation
        return None

    def handle(self, request: IncomingRequest) -> OutgoingResponse:
        if request.path.startswith(CONTROL_PREFIX):
            return self.handle_control(request)
        raw_body = request.body.decode("utf-8", errors="replace")
        route = self.find_route(request.method, request.path)
        if route is None:
            message = f"no operation in the contract matches {request.method} {request.path}"
            self.record(None, request, None, 404, [message])
            return OutgoingResponse.json(404, {"code": "NO_SUCH_OPERATION", "message": message})
        operation_id, operation = route
        try:
            body = json.loads(raw_body) if raw_body.strip() else None
        except ValueError:
            message = "request body is not valid JSON"
            self.record(operation_id, request, raw_body, 422, [message])
            return OutgoingResponse.json(422, {"code": "CONTRACT_VIOLATION", "message": message})
        violations = header_violations(operation.request.headers, request.headers)
        violations += body_violations(operation.request.body, body)
        if violations:
            self.record(operation_id, request, body, 422, violations)
            return OutgoingResponse.json(
                422, {"code": "CONTRACT_VIOLATION", "message": violations[0]}
            )
        return self.respond(operation_id, operation, request, body)

    def respond(
        self, operation_id: str, operation: Operation, request: IncomingRequest, body: Any
    ) -> OutgoingResponse:
        stub = operation.stub
        if stub is None:
            message = f"operation {operation_id} has no stub in the contract"
            self.record(operation_id, request, body, 501, [message])
            return OutgoingResponse.json(501, {"code": "NO_STUB", "message": message})
        key_header = stub.idempotent_on_header
        key = header_value(request.headers, key_header) if key_header else None
        with self.lock:
            replay = self.remembered.get((operation_id, key)) if key is not None else None
            sequence = self.sequences.get(operation_id, 0) + 1
            if replay is None:
                self.sequences[operation_id] = sequence
        if replay is not None:
            status, payload = replay
            self.record(operation_id, request, body, status, [], replayed=True)
            return OutgoingResponse.json(status, payload)
        variables = {"seq": sequence, "request": {"body": body, "headers": dict(request.headers)}}
        try:
            payload = render(stub.body, variables)
        except TemplateError as error:
            message = f"stub body template failed: {error}"
            self.record(operation_id, request, body, 500, [message])
            return OutgoingResponse.json(500, {"code": "STUB_TEMPLATE_ERROR", "message": message})
        with self.lock:
            if key is not None:
                self.remembered[(operation_id, key)] = (stub.default_status, payload)
        self.record(operation_id, request, body, stub.default_status, [])
        return OutgoingResponse.json(stub.default_status, payload)

    def record(
        self,
        operation_id: str | None,
        request: IncomingRequest,
        body: Any,
        status: int,
        violations: list[str],
        replayed: bool = False,
    ) -> None:
        entry = ReceivedRequest(
            operation_id,
            request.method,
            request.path,
            dict(request.headers),
            body,
            status,
            replayed,
            violations,
        )
        with self.lock:
            self.entries.append(entry)

    def handle_control(self, request: IncomingRequest) -> OutgoingResponse:
        action = request.path.removeprefix(CONTROL_PREFIX)
        if request.method == "GET" and action == "log":
            return OutgoingResponse.json(200, [asdict(entry) for entry in self.log()])
        if request.method == "POST" and action == "reset":
            self.reset()
            return OutgoingResponse.json(200, {"status": "reset"})
        if request.method == "POST" and action.startswith("emit/"):
            payload = json.loads(request.body or b"{}")
            status, text = self.emit(
                action.removeprefix("emit/"),
                payload.get("body"),
                payload.get("signature", "valid"),
            )
            return OutgoingResponse.json(200, {"status": status, "body": text})
        return OutgoingResponse.json(404, {"code": "NOT_FOUND", "message": request.path})

    def emit(self, webhook_id: str, body: Any, signature: EmitSignature) -> tuple[int, str]:
        webhook = self.contract.webhooks.get(webhook_id)
        if webhook is None:
            raise ConfigurationError([f"webhook '{webhook_id}' does not exist in the contract"])
        if not self.webhook_receiver_url:
            raise ConfigurationError(["the consumer binding has no webhook_receiver_url"])
        raw_body = json.dumps(body, ensure_ascii=False, separators=(",", ":"))
        headers = {"Content-Type": "application/json"}
        spec = webhook.signature
        if spec is not None and signature != "missing":
            secret = self.secrets[spec.secret]
            if signature == "invalid":
                secret += INVALID_SIGNATURE_SUFFIX
            headers[spec.header] = sign(secret, raw_body, spec.format)
        response = httpx.request(
            webhook.request.method,
            self.webhook_receiver_url,
            content=raw_body.encode("utf-8"),
            headers=headers,
            timeout=10.0,
        )
        return response.status_code, response.text
