import json
from collections.abc import Callable
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError
from ruamel.yaml import YAML

import doubleblind_contract

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"
PACKAGE_DIR = Path(doubleblind_contract.__file__).resolve().parent
SCHEMA_PATH = PACKAGE_DIR / "schema" / "contract.schema.json"

SIGNED = FIXTURES_DIR / "spokes_parcelly_signed.yaml"
MINIMAL = FIXTURES_DIR / "minimal_valid.yaml"
SCHEMA_BROKEN = FIXTURES_DIR / "broken_schema_version_not_integer.yaml"
SEMANTIC_FIXTURES = tuple(
    sorted([*FIXTURES_DIR.glob("broken_rule*.yaml"), *FIXTURES_DIR.glob("warning_rule*.yaml")])
)
STRUCTURALLY_VALID = (SIGNED, MINIMAL, *SEMANTIC_FIXTURES)
ALL_FIXTURES = tuple(sorted(FIXTURES_DIR.glob("*.yaml")))
BINDINGS_DIR = FIXTURES_DIR / "bindings"
SPOKES_BINDING = BINDINGS_DIR / "spokes.binding.yaml"
PARCELLY_BINDING = BINDINGS_DIR / "parcelly.binding.yaml"
BINDING_SCHEMA_PATH = PACKAGE_DIR / "schema" / "binding.schema.json"

Mutation = Callable[[dict[str, Any]], None]
INVALID_MUTATIONS: dict[str, Mutation] = {}


def load_yaml(path: Path) -> Any:
    return YAML(typ="safe").load(path.read_text(encoding="utf-8"))


def minimal_contract() -> dict[str, Any]:
    data: dict[str, Any] = load_yaml(MINIMAL)
    return data


@cache
def schema_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


def schema_errors(data: Any) -> list[ValidationError]:
    return sorted(schema_validator().iter_errors(data), key=lambda error: list(error.absolute_path))


def mutation(function: Mutation) -> Mutation:
    INVALID_MUTATIONS[function.__name__] = function
    return function


def consumer_steps(data: dict[str, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = data["scenarios"][0]["steps"]
    return steps


def provider_send(data: dict[str, Any]) -> dict[str, Any]:
    send: dict[str, Any] = data["scenarios"][1]["steps"][1]["send"]
    return send


@mutation
def missing_scenarios(data: dict[str, Any]) -> None:
    del data["scenarios"]


@mutation
def unknown_top_level_section(data: dict[str, Any]) -> None:
    data["notes"] = "free text"


@mutation
def version_written_as_text(data: dict[str, Any]) -> None:
    data["version"] = "1"


@mutation
def unknown_contract_status(data: dict[str, Any]) -> None:
    data["status"] = "approved"


@mutation
def unknown_step_kind(data: dict[str, Any]) -> None:
    consumer_steps(data)[0] = {"expect_recieved": {"operation": "create_shipment"}}


@mutation
def two_kinds_in_one_step(data: dict[str, Any]) -> None:
    consumer_steps(data)[0] = {
        "do": {"party": "consumer", "action": "pay_order"},
        "expect_no_request": {"within_s": 1},
    }


@mutation
def unknown_field_in_step(data: dict[str, Any]) -> None:
    consumer_steps(data)[0]["do"]["timeout"] = 5


@mutation
def empty_steps(data: dict[str, Any]) -> None:
    data["scenarios"][0]["steps"] = []


@mutation
def unknown_scenario_side(data: dict[str, Any]) -> None:
    data["scenarios"][0]["side"] = "both"


@mutation
def zero_wait_time(data: dict[str, Any]) -> None:
    consumer_steps(data)[2]["expect_received"]["within_s"] = 0


@mutation
def operation_id_not_snake_case(data: dict[str, Any]) -> None:
    data["operations"]["CreateShipment"] = data["operations"].pop("create_shipment")


@mutation
def status_code_key_not_a_number(data: dict[str, Any]) -> None:
    responses = data["operations"]["create_shipment"]["responses"]
    responses["2xx"] = responses.pop("201")


@mutation
def request_path_without_slash(data: dict[str, Any]) -> None:
    data["operations"]["create_shipment"]["request"]["path"] = "v1/shipments"


@mutation
def header_name_with_space(data: dict[str, Any]) -> None:
    data["operations"]["create_shipment"]["request"]["headers"]["Api Key"] = {"required": True}


@mutation
def capture_path_without_dollar(data: dict[str, Any]) -> None:
    provider_send(data)["capture"] = {"shipment_id": "id"}


@mutation
def header_value_not_text(data: dict[str, Any]) -> None:
    provider_send(data)["headers"]["X-Retry"] = 3


@mutation
def emit_with_unknown_signature_mode(data: dict[str, Any]) -> None:
    consumer_steps(data).append(
        {
            "emit": {
                "webhook": "status_changed",
                "body": {"event": "shipment.status_changed"},
                "signature": "forged",
                "expect": {"status": 401},
            }
        }
    )


@mutation
def signature_format_without_hex(data: dict[str, Any]) -> None:
    data["webhooks"]["status_changed"]["signature"]["format"] = "sha256"


@mutation
def unknown_decision_owner(data: dict[str, Any]) -> None:
    data["decisions"][0]["owner"] = "both"


@mutation
def party_without_name(data: dict[str, Any]) -> None:
    del data["parties"]["provider"]["name"]
