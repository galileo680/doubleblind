import json
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx
from jsonschema import Draft202012Validator

from doubleblind_contract.binding import (
    BindingCallError,
    CallKind,
    PartyClient,
    capture_values,
    parse_response_body,
)
from doubleblind_contract.capture_server import CapturedWebhook, WebhookCapture
from doubleblind_contract.loader import format_path
from doubleblind_contract.matcher import match, resolve_path
from doubleblind_contract.models import Contract, PartyRole, Scenario, Side
from doubleblind_contract.runtime import Failure, ScenarioResult, ScenarioStatus
from doubleblind_contract.signing import verify
from doubleblind_contract.stub_server import ProviderStub, ReceivedRequest
from doubleblind_contract.templating import TemplateError, render

DEFAULT_WAIT_S = 2.0
POLL_INTERVAL_S = 0.1
SNIPPET_LENGTH = 300

Check = Callable[[], list[Failure]]


def snippet(text: str) -> str:
    text = text.strip()
    return text if len(text) <= SNIPPET_LENGTH else f"{text[:SNIPPET_LENGTH]}..."


@dataclass
class RunEnvironment:
    contract: Contract
    side: Side
    http: httpx.Client
    secrets: Mapping[str, str]
    env: Mapping[str, str]
    parties: dict[PartyRole, PartyClient] = field(default_factory=dict)
    stub: ProviderStub | None = None
    capture: WebhookCapture | None = None
    provider_base_url: str | None = None
    urls: dict[str, dict[str, str]] = field(default_factory=dict)
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic

    def base_variables(self) -> dict[str, Any]:
        return {"secrets": dict(self.secrets), "env": dict(self.env), **self.urls}


class StepFailed(Exception):
    def __init__(self, failures: list[Failure]) -> None:
        super().__init__("; ".join(failure.message for failure in failures))
        self.failures = failures


class ScenarioExecutor:
    def __init__(self, environment: RunEnvironment, scenario: Scenario) -> None:
        self.environment = environment
        self.scenario = scenario
        self.ctx: dict[str, Any] = {}
        self.step_index = 0

    def variables(self, args: Any = None) -> dict[str, Any]:
        variables = {**self.environment.base_variables(), "ctx": dict(self.ctx)}
        if args is not None:
            variables["args"] = args
        return variables

    def fail(self, path: str, expected: Any, actual: Any, message: str) -> Failure:
        return Failure(self.step_index, path, expected, actual, message)

    def run(self) -> ScenarioResult:
        started = time.monotonic()
        status: ScenarioStatus = "passed"
        failures: list[Failure] = []
        try:
            for index, step in enumerate(self.scenario.steps):
                self.step_index = index
                self.run_step(step.kind, step.model_dump(mode="json", exclude_unset=True))
        except StepFailed as failed:
            failures = failed.failures
        except Exception as error:
            status = "error"
            failures = [self.fail("", None, None, f"{type(error).__name__}: {error}")]
        failures += self.automatic_checks()
        if failures and status == "passed":
            status = "failed"
        return ScenarioResult(
            self.scenario.id,
            self.scenario.title,
            status,
            round((time.monotonic() - started) * 1000),
            failures,
        )

    def run_step(self, kind: str, dumped: Mapping[str, Any]) -> None:
        try:
            spec = render(dumped[kind], self.variables())
        except TemplateError as error:
            raise StepFailed([self.fail("", None, None, f"template error: {error}")]) from error
        getattr(self, f"run_{kind}")(spec)

    def wait_for(self, check: Check, within_s: float) -> list[Failure]:
        deadline = self.environment.clock() + within_s
        while True:
            failures = check()
            if not failures or self.environment.clock() >= deadline:
                return failures
            self.environment.sleep(POLL_INTERVAL_S)

    def party(self, role: PartyRole) -> PartyClient:
        client = self.environment.parties.get(role)
        if client is None:
            raise StepFailed([self.fail("", None, None, f"no {role} binding was given")])
        return client

    def call_party(
        self, role: PartyRole, kind: CallKind, name: str, args: Any
    ) -> tuple[int, Any, str]:
        client = self.party(role)
        try:
            result = client.call(kind, name, self.variables(args))
        except BindingCallError as error:
            raise StepFailed([self.fail(kind, name, None, str(error))]) from error
        except TemplateError as error:
            raise StepFailed([self.fail(kind, None, None, f"template error: {error}")]) from error
        if result.capture_problems:
            raise StepFailed(
                [self.fail("capture", None, None, problem) for problem in result.capture_problems]
            )
        self.ctx.update(result.captured)
        return result.status, result.body, result.text

    def run_do(self, spec: dict[str, Any]) -> None:
        status, _, text = self.call_party(spec["party"], "action", spec["action"], spec.get("args"))
        if status >= 400:
            raise StepFailed(
                [
                    self.fail(
                        "status",
                        "2xx",
                        status,
                        f"{spec['party']} action '{spec['action']}' failed: {snippet(text)}",
                    )
                ]
            )

    def run_send(self, spec: dict[str, Any]) -> None:
        contract = self.environment.contract
        operation = contract.operations[spec["operation"]]
        if self.environment.provider_base_url is None:
            raise StepFailed([self.fail("", None, None, "no provider base URL for send")])
        request: dict[str, Any] = {"headers": spec.get("headers") or {}}
        if "body" in spec:
            request["json"] = spec["body"]
        response = self.environment.http.request(
            operation.request.method,
            f"{self.environment.provider_base_url.rstrip('/')}{operation.request.path}",
            **request,
        )
        body = parse_response_body(response)
        target = {"status": response.status_code, "headers": dict(response.headers), "body": body}
        expect = spec["expect"]
        failures: list[Failure] = []
        if response.status_code != expect["status"]:
            failures.append(
                self.fail(
                    "status",
                    expect["status"],
                    response.status_code,
                    f"unexpected status from {spec['operation']}: {snippet(response.text)}",
                )
            )
        status_key = str(response.status_code)
        if status_key not in operation.responses:
            failures.append(
                self.fail(
                    "status",
                    sorted(operation.responses),
                    response.status_code,
                    "status is not declared in the contract responses",
                )
            )
        else:
            failures += self.schema_failures(operation.responses[status_key].body, body, "body")
        failures += self.match_failures(target, expect.get("match"))
        if failures:
            raise StepFailed(failures)
        if spec.get("capture"):
            values, problems = capture_values(body, spec["capture"])
            if problems:
                raise StepFailed(
                    [self.fail("capture", None, None, problem) for problem in problems]
                )
            self.ctx.update(values)

    def run_emit(self, spec: dict[str, Any]) -> None:
        stub = self.require_stub()
        status, text = stub.emit(spec["webhook"], spec["body"], spec["signature"])
        expected = spec["expect"]["status"]
        if status != expected:
            raise StepFailed(
                [
                    self.fail(
                        "status",
                        expected,
                        status,
                        f"the consumer answered the {spec['webhook']} webhook with {status}: "
                        f"{snippet(text)}",
                    )
                ]
            )

    def run_expect_received(self, spec: dict[str, Any]) -> None:
        stub = self.require_stub()
        operation = spec["operation"]

        def check() -> list[Failure]:
            entries = [entry for entry in stub.log() if entry.operation == operation]
            if not entries:
                return [self.fail("", operation, None, f"no {operation} request was received")]
            failures: list[Failure] = []
            count = spec.get("count")
            if count is not None and len(entries) != count:
                failures.append(
                    self.fail("", count, len(entries), f"expected {count} {operation} request(s)")
                )
            for path, expected_distinct in (spec.get("distinct") or {}).items():
                values = {
                    json.dumps(resolve_path(entry.as_match_target(), path), default=str)
                    for entry in entries
                }
                if len(values) != expected_distinct:
                    failures.append(
                        self.fail(
                            path,
                            expected_distinct,
                            len(values),
                            "unexpected number of distinct values",
                        )
                    )
            for number, entry in enumerate(entries, start=1):
                suffix = f" (request {number} of {len(entries)})" if len(entries) > 1 else ""
                failures += self.match_failures(entry.as_match_target(), spec.get("match"), suffix)
            return failures

        self.raise_if(self.wait_for(check, spec.get("within_s", DEFAULT_WAIT_S)))

    def run_expect_no_request(self, spec: dict[str, Any]) -> None:
        stub = self.require_stub()
        operation = spec.get("operation")

        def unexpected() -> list[ReceivedRequest]:
            return [
                entry for entry in stub.log() if operation is None or entry.operation == operation
            ]

        deadline = self.environment.clock() + spec["within_s"]
        while True:
            found = unexpected()
            if found:
                entry = found[0]
                raise StepFailed(
                    [
                        self.fail(
                            "",
                            "no request",
                            f"{entry.method} {entry.path}",
                            f"a {entry.operation or 'request'} was received but none was expected",
                        )
                    ]
                )
            if self.environment.clock() >= deadline:
                return
            self.environment.sleep(POLL_INTERVAL_S)

    def run_expect_emitted(self, spec: dict[str, Any]) -> None:
        capture = self.require_capture()
        webhook = self.environment.contract.webhooks[spec["webhook"]]

        def check() -> list[Failure]:
            captured = capture.webhooks()
            if not captured:
                return [self.fail("", spec["webhook"], None, "no webhook was received")]
            expected_match = spec.get("match")
            matching = [
                item
                for item in captured
                if not expected_match or not match(item.as_match_target(), expected_match)
            ]
            if not matching:
                return self.match_failures(captured[-1].as_match_target(), expected_match)
            failures: list[Failure] = []
            count = spec.get("count")
            if count is not None and len(matching) != count:
                failures.append(self.fail("", count, len(matching), "unexpected webhook count"))
            for item in matching:
                failures += self.webhook_failures(webhook.request.body, item, spec)
            return failures

        self.raise_if(self.wait_for(check, spec.get("within_s", DEFAULT_WAIT_S)))

    def webhook_failures(
        self, schema: Any, item: CapturedWebhook, spec: Mapping[str, Any]
    ) -> list[Failure]:
        failures = self.schema_failures(schema, item.body, "body")
        signature = self.environment.contract.webhooks[spec["webhook"]].signature
        if spec.get("signature") == "valid" and signature is not None:
            header = item.header(signature.header)
            secret = self.environment.secrets[signature.secret]
            if not verify(secret, item.raw_body, header, signature.format):
                failures.append(
                    self.fail(
                        f"headers.{signature.header}",
                        "valid signature",
                        header,
                        "webhook signature does not match the raw body and the shared secret",
                    )
                )
        return failures

    def run_expect_state(self, spec: dict[str, Any]) -> None:
        def check() -> list[Failure]:
            status, body, text = self.call_party(
                spec["party"], "query", spec["query"], spec.get("args")
            )
            if status >= 400:
                return [
                    self.fail(
                        "status",
                        "2xx",
                        status,
                        f"{spec['party']} query '{spec['query']}' failed: {snippet(text)}",
                    )
                ]
            return self.match_failures(body, spec["match"])

        eventually = spec.get("eventually")
        if eventually is None:
            self.raise_if(check())
            return
        deadline = self.environment.clock() + eventually["timeout_s"]
        while True:
            failures = check()
            if not failures or self.environment.clock() >= deadline:
                self.raise_if(failures)
                return
            self.environment.sleep(eventually["interval_s"])

    def automatic_checks(self) -> list[Failure]:
        if self.environment.side != "consumer" or self.environment.stub is None:
            return []
        return [
            Failure(
                None,
                entry.operation or "",
                "a request that follows the contract",
                f"{entry.method} {entry.path}",
                f"request violated the contract: {violation}",
            )
            for entry in self.environment.stub.log()
            for violation in entry.violations
        ]

    def schema_failures(self, schema: Any, value: Any, prefix: str) -> list[Failure]:
        if schema is None:
            return []
        return [
            self.fail(
                format_path([prefix, *error.absolute_path]),
                None,
                None,
                f"does not match the contract schema: {error.message}",
            )
            for error in Draft202012Validator(schema).iter_errors(value)
        ]

    def match_failures(
        self, target: Any, expected: Mapping[str, Any] | None, suffix: str = ""
    ) -> list[Failure]:
        if not expected:
            return []
        return [
            self.fail(item.path, item.expected, item.to_dict()["actual"], item.message + suffix)
            for item in match(target, expected)
        ]

    def raise_if(self, failures: list[Failure]) -> None:
        if failures:
            raise StepFailed(failures)

    def require_stub(self) -> ProviderStub:
        if self.environment.stub is None:
            raise StepFailed([self.fail("", None, None, "this step needs the provider stub")])
        return self.environment.stub

    def require_capture(self) -> WebhookCapture:
        if self.environment.capture is None:
            raise StepFailed([self.fail("", None, None, "this step needs the capture server")])
        return self.environment.capture
