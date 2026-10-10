from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError as SchemaValidationError

from doubleblind_contract.jsonschemas import binding_validator, contract_validator, meta_validator
from doubleblind_contract.loader import (
    ContractLoadError,
    PathParts,
    Problem,
    build_binding,
    build_contract,
    format_path,
    parse_yaml,
    resolve_refs,
)
from doubleblind_contract.models import (
    Binding,
    Contract,
    DoStep,
    EmitStep,
    ExpectEmittedStep,
    ExpectNoRequestStep,
    ExpectReceivedStep,
    ExpectStateStep,
    PartyRole,
    SendStep,
    Side,
    Step,
)
from doubleblind_contract.templating import (
    expression_root,
    is_whole_template,
    parse_expression,
    replace_templates,
    template_expressions,
)

Severity = Literal["error", "warning"]

RULE_YAML = "yaml"
RULE_SCHEMA = "schema"
RULE_BINDING = "binding"
RULE_REFS = "rule 1"
RULE_REFERENCES = "rule 2"
RULE_STEP_SIDE = "rule 3"
RULE_UNIQUE_IDS = "rule 4"
RULE_COVERAGE = "rule 5"
RULE_STUB_BODY = "rule 6"

ALLOWED_STEPS: Mapping[Side, frozenset[str]] = {
    "consumer": frozenset({"do", "emit", "expect_received", "expect_no_request", "expect_state"}),
    "provider": frozenset({"do", "send", "expect_emitted", "expect_state"}),
    "e2e": frozenset({"do", "send", "expect_state"}),
}
SAMPLE_TEMPLATE_TEXT = {
    "seq": "1",
    "now": "2026-01-01T00:00:00.000Z",
    "uuid": "00000000-0000-4000-8000-000000000000",
}


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: Severity
    path: str
    message: str

    def render(self) -> str:
        location = f"{self.path}: " if self.path else ""
        return f"{self.severity:<7} [{self.rule}] {location}{self.message}"

    def to_dict(self) -> dict[str, str]:
        return {
            "rule": self.rule,
            "severity": self.severity,
            "path": self.path,
            "message": self.message,
        }


@dataclass
class ValidationReport:
    findings: list[Finding] = field(default_factory=list)

    @property
    def errors(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "error"]

    @property
    def warnings(self) -> list[Finding]:
        return [finding for finding in self.findings if finding.severity == "warning"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, rule: str, path: str, message: str) -> None:
        self.findings.append(Finding(rule, "error", path, message))

    def warning(self, rule: str, path: str, message: str) -> None:
        self.findings.append(Finding(rule, "warning", path, message))

    def add_problems(self, rule: str, problems: Iterable[Problem], prefix: str = "") -> None:
        for problem in problems:
            path = f"{prefix}{problem.path}" if prefix else problem.path
            self.error(rule, path, problem.message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "errors": len(self.errors),
            "warnings": len(self.warnings),
            "findings": [finding.to_dict() for finding in self.findings],
        }


def sorted_schema_errors(validator: Draft202012Validator, data: Any) -> list[SchemaValidationError]:
    return sorted(
        validator.iter_errors(data), key=lambda error: [str(part) for part in error.absolute_path]
    )


def schema_problems(validator: Draft202012Validator, data: Any) -> list[Problem]:
    return [
        Problem(format_path(list(error.absolute_path)), error.message)
        for error in sorted_schema_errors(validator, data)
    ]


def data_schema_locations(data: Mapping[str, Any]) -> list[tuple[PathParts, Any]]:
    locations: list[tuple[PathParts, Any]] = [
        (("schemas", name), schema) for name, schema in data.get("schemas", {}).items()
    ]
    for operation_id, operation in data.get("operations", {}).items():
        request_body = operation["request"].get("body")
        if request_body is not None:
            locations.append((("operations", operation_id, "request", "body"), request_body))
        for status, response in operation["responses"].items():
            if response.get("body") is not None:
                locations.append(
                    (("operations", operation_id, "responses", status, "body"), response["body"])
                )
    for webhook_id, webhook in data.get("webhooks", {}).items():
        if webhook["request"].get("body") is not None:
            locations.append(
                (("webhooks", webhook_id, "request", "body"), webhook["request"]["body"])
            )
    return locations


def check_data_schemas(data: Mapping[str, Any], report: ValidationReport) -> None:
    for location, schema in data_schema_locations(data):
        for error in sorted_schema_errors(meta_validator(), schema):
            path = format_path([*location, *error.absolute_path])
            report.error(RULE_SCHEMA, path, f"not a valid JSON Schema: {error.message}")


def step_path(scenario_index: int, step_index: int, step: Step) -> str:
    return f"scenarios[{scenario_index}].steps[{step_index}].{step.kind}"


def check_references(
    contract: Contract, bindings: Mapping[PartyRole, Binding], report: ValidationReport
) -> None:
    for scenario_index, scenario in enumerate(contract.scenarios):
        for step_index, step in enumerate(scenario.steps):
            path = step_path(scenario_index, step_index, step)
            check_step_reference(contract, bindings, step, path, report)
    known_ids = {
        *(decision.id for decision in contract.decisions),
        *contract.operations,
        *contract.webhooks,
    }
    for scenario_index, scenario in enumerate(contract.scenarios):
        for cover_index, covered in enumerate(scenario.covers):
            if covered not in known_ids:
                report.warning(
                    RULE_COVERAGE,
                    f"scenarios[{scenario_index}].covers[{cover_index}]",
                    f"'{covered}' is not a decision, operation or webhook of this contract",
                )
    check_secret_references(contract, report)


def check_step_reference(
    contract: Contract,
    bindings: Mapping[PartyRole, Binding],
    step: Step,
    path: str,
    report: ValidationReport,
) -> None:
    operation: str | None = None
    webhook: str | None = None
    if isinstance(step, SendStep):
        operation = step.send.operation
    elif isinstance(step, ExpectReceivedStep):
        operation = step.expect_received.operation
    elif isinstance(step, ExpectNoRequestStep):
        operation = step.expect_no_request.operation
    elif isinstance(step, EmitStep):
        webhook = step.emit.webhook
    elif isinstance(step, ExpectEmittedStep):
        webhook = step.expect_emitted.webhook
    if operation is not None and operation not in contract.operations:
        report.error(
            RULE_REFERENCES,
            f"{path}.operation",
            f"operation '{operation}' does not exist in operations",
        )
    if webhook is not None and webhook not in contract.webhooks:
        report.error(
            RULE_REFERENCES, f"{path}.webhook", f"webhook '{webhook}' does not exist in webhooks"
        )
    if isinstance(step, DoStep):
        check_binding_name(
            bindings.get(step.do.party), "action", step.do.action, f"{path}.action", report
        )
    elif isinstance(step, ExpectStateStep):
        check_binding_name(
            bindings.get(step.expect_state.party),
            "query",
            step.expect_state.query,
            f"{path}.query",
            report,
        )


def check_binding_name(
    binding: Binding | None,
    kind: Literal["action", "query"],
    name: str,
    path: str,
    report: ValidationReport,
) -> None:
    if binding is None:
        return
    available = binding.actions if kind == "action" else binding.queries
    if name not in available:
        report.error(
            RULE_REFERENCES, path, f"{kind} '{name}' is not defined in the {binding.side} binding"
        )


def check_secret_references(contract: Contract, report: ValidationReport) -> None:
    declared = {secret.name for secret in contract.secrets}
    for webhook_id, webhook in contract.webhooks.items():
        if webhook.signature is not None and webhook.signature.secret not in declared:
            report.error(
                RULE_REFERENCES,
                f"webhooks.{webhook_id}.signature.secret",
                f"secret '{webhook.signature.secret}' is not declared in secrets",
            )
    for scenario_index, scenario in enumerate(contract.scenarios):
        steps = [step.model_dump(mode="json", exclude_unset=True) for step in scenario.steps]
        for expression in template_expressions(steps):
            root, parts = parse_expression(expression)
            if root != "secrets" or not parts:
                continue
            name = parts[0]
            if name not in declared:
                report.error(
                    RULE_REFERENCES,
                    f"scenarios[{scenario_index}]",
                    f"template '{{{{{expression}}}}}' uses secret '{name}', "
                    "which is not declared in secrets",
                )


def check_step_sides(contract: Contract, report: ValidationReport) -> None:
    for scenario_index, scenario in enumerate(contract.scenarios):
        allowed = ALLOWED_STEPS[scenario.side]
        for step_index, step in enumerate(scenario.steps):
            path = step_path(scenario_index, step_index, step)
            if step.kind not in allowed:
                report.error(
                    RULE_STEP_SIDE,
                    path,
                    f"step '{step.kind}' is not allowed in a {scenario.side} scenario "
                    f"(allowed: {', '.join(sorted(allowed))})",
                )
                continue
            party = party_of(step)
            if party is not None and scenario.side != "e2e" and party != scenario.side:
                report.error(
                    RULE_STEP_SIDE,
                    f"{path}.party",
                    f"a {scenario.side} scenario can only act as {scenario.side}, not {party}",
                )


def party_of(step: Step) -> PartyRole | None:
    if isinstance(step, DoStep):
        return step.do.party
    if isinstance(step, ExpectStateStep):
        return step.expect_state.party
    return None


def check_unique_ids(contract: Contract, report: ValidationReport) -> None:
    for section, ids in (
        ("scenarios", [scenario.id for scenario in contract.scenarios]),
        ("decisions", [decision.id for decision in contract.decisions]),
    ):
        first_seen: dict[str, int] = {}
        for index, item_id in enumerate(ids):
            if item_id in first_seen:
                report.error(
                    RULE_UNIQUE_IDS,
                    f"{section}[{index}].id",
                    f"id '{item_id}' is already used by {section}[{first_seen[item_id]}]",
                )
            else:
                first_seen[item_id] = index


def check_decision_coverage(contract: Contract, report: ValidationReport) -> None:
    covered = {item for scenario in contract.scenarios for item in scenario.covers}
    for index, decision in enumerate(contract.decisions):
        if decision.id not in covered:
            report.warning(
                RULE_COVERAGE,
                f"decisions[{index}]",
                f"decision {decision.id} is not covered by any scenario",
            )


def sample_stub_body(value: Any, path: PathParts, unknown: list[PathParts]) -> Any:
    if isinstance(value, str):
        if is_whole_template(value):
            unknown.append(path)
            return None
        return replace_templates(
            value, lambda expression: SAMPLE_TEMPLATE_TEXT.get(expression_root(expression), "x")
        )
    if isinstance(value, Mapping):
        return {key: sample_stub_body(item, (*path, key), unknown) for key, item in value.items()}
    if isinstance(value, list):
        return [sample_stub_body(item, (*path, index), unknown) for index, item in enumerate(value)]
    return value


def check_stub_bodies(contract: Contract, report: ValidationReport) -> None:
    for operation_id, operation in contract.operations.items():
        stub = operation.stub
        if stub is None:
            continue
        base = f"operations.{operation_id}.stub"
        status = str(stub.default_status)
        if status not in operation.responses:
            report.error(
                RULE_STUB_BODY,
                f"{base}.default_status",
                f"status {status} is not listed in responses ({', '.join(operation.responses)})",
            )
            continue
        schema = operation.responses[status].body
        if schema is None:
            continue
        unknown: list[PathParts] = []
        sample = sample_stub_body(stub.body, (), unknown)
        for error in Draft202012Validator(schema).iter_errors(sample):
            location = tuple(error.absolute_path)
            if any(location[: len(prefix)] == prefix for prefix in unknown):
                continue
            report.error(
                RULE_STUB_BODY,
                format_path(["operations", operation_id, "stub", "body", *location]),
                f"does not match the {status} response schema: {error.message}",
            )


def validate_contract_data(
    data: Mapping[str, Any], bindings: Sequence[Binding] = ()
) -> ValidationReport:
    report = ValidationReport()
    structure = schema_problems(contract_validator(), data)
    if structure:
        report.add_problems(RULE_SCHEMA, structure)
        return report
    check_data_schemas(data, report)
    contract = build_contract(data)
    resolved: Contract | None = None
    try:
        resolved = build_contract(resolve_refs(data))
    except ContractLoadError as error:
        report.add_problems(RULE_REFS, error.problems)
    check_references(contract, {binding.side: binding for binding in bindings}, report)
    check_step_sides(contract, report)
    check_unique_ids(contract, report)
    check_decision_coverage(contract, report)
    if resolved is not None:
        check_stub_bodies(resolved, report)
    return report


def validate_contract_text(text: str, bindings: Sequence[Binding] = ()) -> ValidationReport:
    try:
        data = parse_yaml(text)
    except ContractLoadError as error:
        report = ValidationReport()
        report.add_problems(RULE_YAML, error.problems)
        return report
    return validate_contract_data(data, bindings)


def validate_contract_file(path: str | Path, bindings: Sequence[Binding] = ()) -> ValidationReport:
    return validate_contract_text(Path(path).read_text(encoding="utf-8"), bindings)


def check_binding_text(text: str) -> tuple[Binding | None, ValidationReport]:
    report = ValidationReport()
    try:
        data = parse_yaml(text)
    except ContractLoadError as error:
        report.add_problems(RULE_YAML, error.problems)
        return None, report
    structure = schema_problems(binding_validator(), data)
    if structure:
        report.add_problems(RULE_BINDING, structure)
        return None, report
    try:
        return build_binding(data), report
    except ContractLoadError as error:
        report.add_problems(RULE_BINDING, error.problems)
        return None, report
