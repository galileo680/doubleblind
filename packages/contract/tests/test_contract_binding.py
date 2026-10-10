import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from contract_fixtures import BINDING_SCHEMA_PATH, PARCELLY_BINDING, SPOKES_BINDING, load_yaml
from jsonschema import Draft202012Validator
from pydantic import ValidationError

from doubleblind_contract.loader import ContractLoadError, load_binding, load_binding_text
from doubleblind_contract.models import Binding

BindingMutation = Callable[[dict[str, Any]], None]


def without_webhook_receiver(data: dict[str, Any]) -> None:
    del data["webhook_receiver_url"]


def managed_app_without_start(data: dict[str, Any]) -> None:
    del data["app"]["start"]


def unknown_field(data: dict[str, Any]) -> None:
    data["timeout"] = 5


def path_without_slash(data: dict[str, Any]) -> None:
    data["actions"]["reset"]["path"] = "__doubleblind/reset"


def capture_without_dollar(data: dict[str, Any]) -> None:
    data["actions"]["create_order"]["capture"] = {"order_id": "id"}


def env_value_not_text(data: dict[str, Any]) -> None:
    data["app"]["env"]["PORT"] = 8000


def unknown_side(data: dict[str, Any]) -> None:
    data["side"] = "e2e"


def action_name_not_snake_case(data: dict[str, Any]) -> None:
    data["actions"]["PayOrder"] = data["actions"].pop("pay_order")


MUTATIONS: dict[str, BindingMutation] = {
    function.__name__: function
    for function in (
        without_webhook_receiver,
        managed_app_without_start,
        unknown_field,
        path_without_slash,
        capture_without_dollar,
        env_value_not_text,
        unknown_side,
        action_name_not_snake_case,
    )
}


def binding_schema_errors(data: Any) -> list[str]:
    schema = json.loads(BINDING_SCHEMA_PATH.read_text(encoding="utf-8"))
    return [error.message for error in Draft202012Validator(schema).iter_errors(data)]


def test_binding_schema_is_a_valid_json_schema() -> None:
    Draft202012Validator.check_schema(json.loads(BINDING_SCHEMA_PATH.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", [SPOKES_BINDING, PARCELLY_BINDING], ids=lambda path: path.name)
def test_example_bindings_pass_schema_and_models(path: Path) -> None:
    data = load_yaml(path)

    assert binding_schema_errors(data) == []
    assert Binding.model_validate(data).model_dump(mode="json", exclude_unset=True) == data


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_schema_and_models_reject_the_same_broken_bindings(name: str) -> None:
    data = load_yaml(SPOKES_BINDING)
    MUTATIONS[name](data)

    assert binding_schema_errors(data) != []
    with pytest.raises(ValidationError):
        Binding.model_validate(data)


def test_provider_binding_does_not_need_a_webhook_receiver() -> None:
    binding = load_binding(PARCELLY_BINDING)

    assert binding.side == "provider"
    assert binding.webhook_receiver_url is None
    assert binding.app is not None
    assert binding.app.healthcheck is not None
    assert binding.app.healthcheck.method == "GET"


def test_binding_is_readable_as_objects() -> None:
    binding = load_binding(SPOKES_BINDING)

    assert binding.actions["create_order"].body == "{{args}}"
    assert binding.actions["create_order"].capture == {"order_id": "$.id"}
    assert binding.queries["order"].path == "/orders/{{ctx.order_id}}"


def test_binding_environment_placeholders_are_filled_when_env_is_given() -> None:
    binding = load_binding(SPOKES_BINDING, env={"CONSUMER_BASE_URL": "http://127.0.0.1:8000"})

    assert binding.base_url == "http://127.0.0.1:8000"
    assert binding.webhook_receiver_url == "http://127.0.0.1:8000/integrations/parcelly/webhook"
    assert binding.app is not None
    assert binding.app.env["PARCELLY_BASE_URL"] == "{{stub.url}}"


def test_broken_binding_reports_the_reason() -> None:
    text = SPOKES_BINDING.read_text(encoding="utf-8").replace("  start: ", "  begin: ")

    with pytest.raises(ContractLoadError) as caught:
        load_binding_text(text)

    assert any(problem.path == "app.begin" for problem in caught.value.problems)
