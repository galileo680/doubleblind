from pathlib import Path

import pytest
from contract_fixtures import (
    ALL_FIXTURES,
    INVALID_MUTATIONS,
    SIGNED,
    STRUCTURALLY_VALID,
    load_yaml,
    minimal_contract,
    schema_errors,
)
from pydantic import ValidationError

from doubleblind_contract.models import (
    Contract,
    DoStep,
    ExpectReceivedStep,
    ExpectStateStep,
    SendStep,
)


def signed_contract() -> Contract:
    return Contract.model_validate(load_yaml(SIGNED))


def test_signed_contract_is_readable_as_objects() -> None:
    contract = signed_contract()

    create_shipment = contract.operations["create_shipment"]
    assert create_shipment.request.method == "POST"
    assert create_shipment.request.path == "/v1/shipments"
    assert create_shipment.request.headers["Idempotency-Key"].required is True
    assert create_shipment.stub is not None
    assert create_shipment.stub.idempotent_on_header == "Idempotency-Key"
    assert set(create_shipment.responses) == {"201", "400", "422"}

    webhook = contract.webhooks["shipment_status_changed"]
    assert webhook.signature is not None
    assert webhook.signature.secret == "webhook_secret"
    assert contract.parties.provider.id == "parcelly"
    assert contract.semantic_mappings["shipment_status_to_order_status"]["RETURNED"] == "returned"


def test_steps_become_typed_objects() -> None:
    contract = signed_contract()
    first_scenario = contract.scenarios[0]

    assert [step.kind for step in first_scenario.steps] == ["do", "do", "do", "expect_received"]
    reset = first_scenario.steps[0]
    assert isinstance(reset, DoStep)
    assert reset.do.party == "consumer"
    assert reset.do.action == "reset"
    received = first_scenario.steps[3]
    assert isinstance(received, ExpectReceivedStep)
    assert received.expect_received.operation == "create_shipment"
    assert received.expect_received.within_s == 5

    send = contract.scenarios_for("provider")[2].steps[2]
    assert isinstance(send, SendStep)
    assert send.send.capture == {"shipment_id": "$.id"}

    eventually = contract.scenarios_for("e2e")[0].steps[6]
    assert isinstance(eventually, ExpectStateStep)
    assert eventually.expect_state.eventually is not None
    assert eventually.expect_state.eventually.timeout_s == 10


def test_scenarios_can_be_filtered_by_side() -> None:
    contract = signed_contract()

    assert [scenario.id for scenario in contract.scenarios_for("provider")] == [
        "P1",
        "P2",
        "P3",
        "P4",
    ]
    assert [scenario.id for scenario in contract.scenarios_for("e2e")] == ["E1", "E2"]


@pytest.mark.parametrize("path", STRUCTURALLY_VALID, ids=lambda path: path.name)
def test_models_keep_the_contract_unchanged(path: Path) -> None:
    data = load_yaml(path)

    dumped = Contract.model_validate(data).model_dump(mode="json", exclude_unset=True)

    assert dumped == data


@pytest.mark.parametrize("path", ALL_FIXTURES, ids=lambda path: path.name)
def test_models_and_schema_agree_on_fixtures(path: Path) -> None:
    data = load_yaml(path)
    accepted_by_schema = schema_errors(data) == []

    try:
        Contract.model_validate(data)
        accepted_by_models = True
    except ValidationError:
        accepted_by_models = False

    assert accepted_by_models == accepted_by_schema


@pytest.mark.parametrize("name", sorted(INVALID_MUTATIONS))
def test_models_reject_invalid_contracts(name: str) -> None:
    data = minimal_contract()
    INVALID_MUTATIONS[name](data)

    with pytest.raises(ValidationError):
        Contract.model_validate(data)


def test_unknown_step_kind_is_named_in_the_error() -> None:
    data = minimal_contract()
    INVALID_MUTATIONS["unknown_step_kind"](data)

    with pytest.raises(ValidationError, match="expect_recieved"):
        Contract.model_validate(data)


def test_step_with_two_kinds_explains_the_rule() -> None:
    data = minimal_contract()
    INVALID_MUTATIONS["two_kinds_in_one_step"](data)

    with pytest.raises(ValidationError, match="exactly one key"):
        Contract.model_validate(data)


def test_contract_objects_cannot_be_modified() -> None:
    contract = signed_contract()

    with pytest.raises(ValidationError):
        contract.version = 10
