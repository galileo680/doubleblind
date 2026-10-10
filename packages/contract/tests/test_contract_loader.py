import json
from copy import deepcopy

import pytest
from contract_fixtures import FIXTURES_DIR, MINIMAL, SCHEMA_BROKEN, SIGNED

from doubleblind_contract.loader import (
    ContractLoadError,
    Problem,
    interpolate_env,
    load_contract,
    load_contract_text,
    parse_yaml,
    resolve_refs,
)
from doubleblind_contract.models import Contract

TEST_ENV = {
    "PROVIDER_BASE_URL": "http://127.0.0.1:8080",
    "CONSUMER_WEBHOOK_URL": "http://127.0.0.1:8000/integrations/courier/webhook",
}


def minimal_text(old: str = "", new: str = "") -> str:
    text = MINIMAL.read_text(encoding="utf-8")
    if old:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text


def test_loads_the_reference_contract_from_a_file() -> None:
    contract = load_contract(SIGNED)

    assert isinstance(contract, Contract)
    assert contract.version == 9
    assert contract.decisions[0].text.startswith("Konsument przelicza kg na g")


def test_accepts_the_path_as_text() -> None:
    assert load_contract(str(SIGNED)).id == "spokes-parcelly-shipping"


def test_replaces_every_ref_with_the_schema_it_points_to() -> None:
    contract = load_contract(SIGNED)

    assert "$ref" not in json.dumps(contract.model_dump(mode="json"))
    body = contract.operations["create_shipment"].request.body
    assert isinstance(body, dict)
    address = body["properties"]["receiver"]["properties"]["address"]
    assert address["properties"]["postcode"]["pattern"] == "^\\d{2}-\\d{3}$"
    error = contract.operations["create_shipment"].responses["422"].body
    assert isinstance(error, dict)
    assert error["required"] == ["code", "message"]


def test_resolves_refs_inside_the_schemas_section() -> None:
    contract = load_contract(SIGNED)

    receiver = contract.schemas["Receiver"]
    assert isinstance(receiver, dict)
    assert receiver["properties"]["address"]["required"] == [
        "street",
        "building",
        "postcode",
        "city",
        "country",
    ]


def test_inlined_schemas_are_independent_copies() -> None:
    data = resolve_refs(parse_yaml(minimal_text()))

    inlined = data["operations"]["create_shipment"]["responses"]["201"]["body"]
    assert inlined == data["schemas"]["Shipment"]
    assert inlined is not data["schemas"]["Shipment"]


def test_resolving_refs_does_not_change_the_input() -> None:
    raw = parse_yaml(minimal_text())
    original = deepcopy(raw)

    resolve_refs(raw)

    assert raw == original


def test_reports_a_ref_to_a_missing_schema_with_its_location() -> None:
    with pytest.raises(ContractLoadError) as caught:
        load_contract(FIXTURES_DIR / "broken_rule1_unresolved_ref.yaml")

    assert caught.value.problems == [
        Problem(
            "operations.create_shipment.responses.201.body",
            "$ref '#/schemas/Shipmnet' points to a schema that does not exist",
        )
    ]


@pytest.mark.parametrize("reference", ["#/definitions/Shipment", "other.yaml#/Shipment"])
def test_reports_unsupported_ref_formats(reference: str) -> None:
    text = minimal_text('"#/schemas/Shipment"', f'"{reference}"')

    with pytest.raises(ContractLoadError) as caught:
        load_contract_text(text)

    assert "only '#/schemas/<Name>' is allowed" in caught.value.problems[0].message


def test_reports_a_ref_that_is_not_text() -> None:
    text = minimal_text('{ $ref: "#/schemas/Shipment" }', "{ $ref: 5 }")

    with pytest.raises(ContractLoadError, match="must be text"):
        load_contract_text(text)


def test_reports_a_circular_ref_once() -> None:
    text = minimal_text(
        "  StatusChanged:\n",
        "  Node:\n"
        "    type: object\n"
        "    properties:\n"
        '      next: { $ref: "#/schemas/Link" }\n'
        "  Link:\n"
        "    type: object\n"
        "    properties:\n"
        '      back: { $ref: "#/schemas/Node" }\n'
        "  StatusChanged:\n",
    )

    with pytest.raises(ContractLoadError) as caught:
        load_contract_text(text)

    assert len(caught.value.problems) == 1
    assert caught.value.problems[0].path == "schemas.Link.properties.back"
    assert "Node -> Link -> Node" in caught.value.problems[0].message


def test_keeps_keywords_written_next_to_a_ref() -> None:
    text = minimal_text(
        '"201": { body: { $ref: "#/schemas/Shipment" } }',
        '"201": { body: { $ref: "#/schemas/Shipment", description: "Created shipment" } }',
    )

    body = load_contract_text(text).operations["create_shipment"].responses["201"].body

    assert isinstance(body, dict)
    assert body["description"] == "Created shipment"
    assert body["allOf"][0]["required"] == ["id", "reference", "status"]


def test_keeps_environment_placeholders_when_no_env_is_given() -> None:
    contract = load_contract(SIGNED)

    assert contract.environments["provider_base_url"] == "${PROVIDER_BASE_URL}"
    assert contract.webhooks["shipment_status_changed"].request.url == "${CONSUMER_WEBHOOK_URL}"


def test_substitutes_environment_values() -> None:
    contract = load_contract(MINIMAL, env=TEST_ENV)

    assert contract.environments["provider_base_url"] == "http://127.0.0.1:8080"
    assert contract.webhooks["status_changed"].request.url == TEST_ENV["CONSUMER_WEBHOOK_URL"]


def test_substitutes_a_variable_inside_longer_text() -> None:
    text = minimal_text(
        '      url: "${CONSUMER_WEBHOOK_URL}"',
        '      url: "${CONSUMER_BASE_URL}/integrations/webhook"',
    )

    contract = load_contract_text(text, env={**TEST_ENV, "CONSUMER_BASE_URL": "http://shop"})

    assert contract.webhooks["status_changed"].request.url == "http://shop/integrations/webhook"


def test_reports_every_missing_environment_variable() -> None:
    with pytest.raises(ContractLoadError) as caught:
        load_contract(MINIMAL, env={})

    assert caught.value.problems == [
        Problem(
            "environments.provider_base_url",
            "environment variable PROVIDER_BASE_URL is not set",
        ),
        Problem(
            "environments.consumer_webhook_url",
            "environment variable CONSUMER_WEBHOOK_URL is not set",
        ),
        Problem(
            "webhooks.status_changed.request.url",
            "environment variable CONSUMER_WEBHOOK_URL is not set",
        ),
    ]


def test_interpolation_leaves_runtime_templates_untouched() -> None:
    contract = load_contract(MINIMAL, env=TEST_ENV)

    stub = contract.operations["create_shipment"].stub
    assert stub is not None
    assert stub.body["id"] == "shp_{{seq}}"
    data = interpolate_env({"header": "Bearer {{secrets.partner_api_key}}"}, TEST_ENV)
    assert data == {"header": "Bearer {{secrets.partner_api_key}}"}


def test_reports_yaml_syntax_errors_with_the_line() -> None:
    with pytest.raises(ContractLoadError) as caught:
        load_contract_text('doubleblind: "1.0"\nid: [unclosed\n')

    assert caught.value.problems[0].path.startswith("line ")


def test_reports_duplicate_keys() -> None:
    text = minimal_text("version: 1\n", "version: 1\nversion: 2\n")

    with pytest.raises(ContractLoadError, match='duplicate key "version"'):
        load_contract_text(text)


@pytest.mark.parametrize("text", ["- just\n- a list\n", "", "just text\n"])
def test_rejects_a_document_that_is_not_a_mapping(text: str) -> None:
    with pytest.raises(ContractLoadError, match="YAML mapping"):
        load_contract_text(text)


def test_reads_yes_no_on_off_as_text() -> None:
    assert parse_yaml("country: NO\nflag: on\nanswer: yes\n") == {
        "country": "NO",
        "flag": "on",
        "answer": "yes",
    }


def test_turns_numeric_keys_and_dates_into_text() -> None:
    assert parse_yaml("201: created\nday: 2026-10-06\n2026-10-07: next\n") == {
        "201": "created",
        "day": "2026-10-06",
        "2026-10-07": "next",
    }


def test_accepts_status_codes_written_without_quotes() -> None:
    text = minimal_text('"201": { body:', "201: { body:")

    contract = load_contract_text(text)

    assert set(contract.operations["create_shipment"].responses) == {"201"}


def test_reports_structure_errors_with_their_location() -> None:
    with pytest.raises(ContractLoadError) as caught:
        load_contract(SCHEMA_BROKEN)

    assert [problem.path for problem in caught.value.problems] == ["version"]


def test_structure_error_paths_for_steps_are_readable() -> None:
    text = minimal_text(
        "- do: { party: consumer, action: pay_order }",
        "- do: { party: consumer, action: pay_order, timeout: 5 }",
    )

    with pytest.raises(ContractLoadError) as caught:
        load_contract_text(text)

    assert [problem.path for problem in caught.value.problems] == [
        "scenarios[0].steps[1].do.timeout"
    ]


def test_all_load_errors_share_one_base_class_and_render_their_problems() -> None:
    with pytest.raises(ContractLoadError) as caught:
        load_contract(FIXTURES_DIR / "broken_rule1_unresolved_ref.yaml")

    assert str(caught.value).startswith("operations.create_shipment.responses.201.body: ")
