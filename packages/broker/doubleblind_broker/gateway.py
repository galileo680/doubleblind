import asyncio
import base64
import hmac
import json
import re
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator

from doubleblind_broker.tokens import hash_token, new_token
from doubleblind_contract.loader import format_path
from doubleblind_contract.models import Binding, Contract, HttpCall

TEMPLATE_SEGMENT = re.compile(r"\{\{[^}]*\}\}|\{[A-Za-z_][A-Za-z0-9_]*\}")
DROPPED_HEADERS = frozenset(
    {
        "host",
        "content-length",
        "connection",
        "keep-alive",
        "transfer-encoding",
        "accept-encoding",
        "cookie",
        "x-relay-token",
    }
)
DEFAULT_TIMEOUT_S = 20.0


def path_regex(template: str) -> re.Pattern[str]:
    parts = TEMPLATE_SEGMENT.split(template)
    return re.compile("^" + "[^/]+".join(re.escape(part) for part in parts) + "$")


def url_path(url: str) -> str:
    if url.startswith("${"):
        return url[url.index("}") + 1 :] or "/"
    return urlsplit(url).path or "/"


@dataclass(frozen=True)
class AllowedCall:
    method: str
    pattern: re.Pattern[str]
    source: str
    request_schema: Any = None
    response_schemas: Mapping[str, Any] = field(default_factory=dict)


@dataclass
class Allowlist:
    calls: list[AllowedCall] = field(default_factory=list)

    def match(self, method: str, path: str) -> AllowedCall | None:
        for call in self.calls:
            if call.method == method and call.pattern.match(path):
                return call
        return None


def binding_calls(binding: Binding, prefix: str) -> list[AllowedCall]:
    calls: list[tuple[str, HttpCall]] = [
        *((f"{prefix} action {name}", call) for name, call in binding.actions.items()),
        *((f"{prefix} query {name}", call) for name, call in binding.queries.items()),
    ]
    allowed = [AllowedCall(call.method, path_regex(call.path), source) for source, call in calls]
    if binding.app is not None and binding.app.healthcheck is not None:
        health = binding.app.healthcheck
        allowed.append(AllowedCall(health.method, path_regex(health.path), f"{prefix} healthcheck"))
    return allowed


def build_allowlist(contract: Contract, binding: Binding | None, role: str) -> Allowlist:
    calls: list[AllowedCall] = []
    if role == "provider":
        for name, operation in contract.operations.items():
            calls.append(
                AllowedCall(
                    operation.request.method,
                    path_regex(operation.request.path),
                    f"operation {name}",
                    operation.request.body,
                    {status: response.body for status, response in operation.responses.items()},
                )
            )
    elif binding is not None and binding.webhook_receiver_url:
        for name, webhook in contract.webhooks.items():
            calls.append(
                AllowedCall(
                    webhook.request.method,
                    path_regex(url_path(binding.webhook_receiver_url)),
                    f"webhook {name}",
                    webhook.request.body,
                )
            )
    if binding is not None:
        calls += binding_calls(binding, role)
    return Allowlist(calls)


def schema_violations(schema: Any, body: bytes, label: str) -> list[str]:
    if schema is None:
        return []
    try:
        value = json.loads(body) if body else None
    except ValueError:
        return [f"{label}: not valid JSON"]
    return [
        f"{format_path([label, *error.absolute_path])}: {error.message}"
        for error in Draft202012Validator(schema).iter_errors(value)
    ]


@dataclass
class RelayedResponse:
    status: int
    headers: dict[str, str]
    body: bytes


@dataclass
class Channel:
    queue: asyncio.Queue[dict[str, Any]] = field(default_factory=asyncio.Queue)
    waiting: dict[str, asyncio.Future[RelayedResponse]] = field(default_factory=dict)


AllowlistSource = Callable[[str, str], Allowlist | None]
EventSink = Callable[[str, dict[str, Any]], None]


class GatewayError(Exception):
    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


class IntegrationGateway:
    def __init__(
        self,
        allowlists: AllowlistSource,
        on_event: EventSink,
        timeout_s: float = DEFAULT_TIMEOUT_S,
    ) -> None:
        self.allowlists = allowlists
        self.on_event = on_event
        self.timeout_s = timeout_s
        self.tokens: dict[tuple[str, str], str] = {}
        self.channels: dict[tuple[str, str], Channel] = {}

    def issue_token(self, session_id: str, party: str) -> str:
        token = new_token()
        self.tokens[(session_id, party)] = hash_token(token)
        return token

    def revoke(self, session_id: str) -> None:
        for key in [key for key in self.tokens if key[0] == session_id]:
            del self.tokens[key]

    def authorize(self, session_id: str, party: str, token: str | None) -> Channel:
        expected = self.tokens.get((session_id, party))
        if (
            expected is None
            or token is None
            or not hmac.compare_digest(expected, hash_token(token))
        ):
            raise GatewayError(401, "BAD_RELAY_TOKEN", "unknown relay token")
        return self.channels.setdefault((session_id, party), Channel())

    async def next_request(
        self, session_id: str, party: str, token: str | None, wait_s: float
    ) -> dict[str, Any] | None:
        channel = self.authorize(session_id, party, token)
        deadline = time.monotonic() + wait_s
        while True:
            try:
                request = await asyncio.wait_for(
                    channel.queue.get(), max(0.0, deadline - time.monotonic())
                )
            except TimeoutError:
                return None
            if request["request_id"] in channel.waiting:
                return request

    def respond(
        self,
        session_id: str,
        party: str,
        token: str | None,
        request_id: str,
        body: Mapping[str, Any],
    ) -> None:
        channel = self.authorize(session_id, party, token)
        future = channel.waiting.pop(request_id, None)
        if future is None or future.done():
            raise GatewayError(404, "NO_SUCH_REQUEST", f"request {request_id} is not waiting")
        future.set_result(
            RelayedResponse(
                int(body.get("status", 502)),
                dict(body.get("headers") or {}),
                base64.b64decode(body.get("body_b64") or ""),
            )
        )

    async def forward(
        self,
        session_id: str,
        party: str,
        method: str,
        path: str,
        query: str,
        headers: Mapping[str, str],
        body: bytes,
    ) -> RelayedResponse:
        started = time.monotonic()
        allowlist = self.allowlists(session_id, party)
        call = allowlist.match(method, path) if allowlist is not None else None
        event: dict[str, Any] = {
            "party": party,
            "method": method,
            "path": path,
            "allowed": call is not None,
        }
        if call is None:
            self.on_event(session_id, {**event, "status": 403, "ms": 0})
            raise GatewayError(
                403, "NOT_IN_CONTRACT", f"{method} {path} is not allowed for {party}"
            )
        channel = self.channels.setdefault((session_id, party), Channel())
        request_id = uuid.uuid4().hex
        future: asyncio.Future[RelayedResponse] = asyncio.get_running_loop().create_future()
        channel.waiting[request_id] = future
        forwarded = {
            key: value for key, value in headers.items() if key.lower() not in DROPPED_HEADERS
        }
        channel.queue.put_nowait(
            {
                "request_id": request_id,
                "method": method,
                "path": path,
                "query": query,
                "headers": forwarded,
                "body_b64": base64.b64encode(body).decode("ascii"),
            }
        )
        try:
            response = await asyncio.wait_for(future, self.timeout_s)
        except TimeoutError as error:
            channel.waiting.pop(request_id, None)
            self.on_event(session_id, {**event, "status": 504, "ms": self.elapsed(started)})
            raise GatewayError(504, "RELAY_TIMEOUT", f"{party} did not answer in time") from error
        violations = schema_violations(call.request_schema, body, "request")
        violations += schema_violations(
            call.response_schemas.get(str(response.status)), response.body, "response"
        )
        self.on_event(
            session_id,
            {
                **event,
                "source": call.source,
                "status": response.status,
                "ms": self.elapsed(started),
                "violations": violations,
            },
        )
        return response

    def elapsed(self, started: float) -> int:
        return round((time.monotonic() - started) * 1000)
