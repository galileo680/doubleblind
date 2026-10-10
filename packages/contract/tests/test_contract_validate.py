from pathlib import Path

import pytest
from contract_fixtures import (
    FIXTURES_DIR,
    MINIMAL,
    PARCELLY_BINDING,
    SCHEMA_BROKEN,
    SIGNED,
    SPOKES_BINDING,
)

from doubleblind_contract.loader import load_binding, load_binding_text
from doubleblind_contract.validate import (
    Finding,
    ValidationReport,
    check_binding_text,
    validate_contract_file,
    validate_contract_text,
)


def minimal_text(old: str = "", new: str = "") -> str:
    text = MINIMAL.read_text(encoding="utf-8")
    if old:
        assert text.count(old) == 1, old
        text = text.replace(old, new)
    return text


def findings_of(text: str) -> list[tuple[str, str, str]]:
    report = validate_contract_text(text)
    return [(finding.rule, finding.severity, finding.path) for finding in report.findings]


def test_reference_contract_has_no_errors_and_no_warnings() -> None:
    report = validate_contract_file(SIGNED)

    assert report.findings == []
    assert report.ok


def test_reference_contract_matches_both_example_bindings() -> None:
    bindings = [load_binding(SPOKES_BINDING), load_binding(PARCELLY_BINDING)]

    report = validate_contract_file(SIGNED, bindings)

    assert report.findings == []


def test_minimal_contract_is_valid() -> None:
    assert validate_contract_file(MINIMAL).findings == []


@pytest.mark.parametrize(
    ("file_name", "expected"),
    [
        (
            "broken_rule1_unresolved_ref.yaml",
            Finding(
                "rule 1",
                "error",
                "operations.create_shipment.responses.201.body",
                "$ref '#/schemas/Shipmnet' points to a schema that does not exist",
            ),
        ),
        (
            "broken_rule2_unknown_operation.yaml",
            Finding(
                "rule 2",
                "error",
                "scenarios[0].steps[2].expect_received.operation",
                "operation 'cancel_shipment' does not exist in operations",
            ),
        ),
        (
            "broken_rule3_step_not_allowed_for_side.yaml",
            Finding(
                "rule 3",
                "error",
                "scenarios[0].steps[2].send",
                "step 'send' is not allowed in a consumer scenario "
                "(allowed: do, emit, expect_no_request, expect_received, expect_state)",
            ),
        ),
        (
            "broken_rule4_duplicate_ids.yaml",
            Finding(
                "rule 4", "error", "scenarios[1].id", "id 'S1' is already used by scenarios[0]"
            ),
        ),
        (
            "warning_rule5_uncovered_decision.yaml",
            Finding(
                "rule 5", "warning", "decisions[1]", "decision D2 is not covered by any scenario"
            ),
        ),
        (
            "broken_rule6_stub_body_mismatch.yaml",
            Finding(
                "rule 6",
                "error",
                "operations.create_shipment.stub.body.price_gr",
                "does not match the 201 response schema: '15.99' is not of type 'integer'",
            ),
        ),
    ],
)
def test_each_broken_fixture_gives_exactly_its_finding(file_name: str, expected: Finding) -> None:
    report = validate_contract_file(FIXTURES_DIR / file_name)

    assert report.findings == [expected]
    assert report.ok == (expected.severity == "warning")


def test_structure_errors_stop_further_checks() -> None:
    report = validate_contract_file(SCHEMA_BROKEN)

    assert report.findings == [
        Finding("schema", "error", "version", "'nine' is not of type 'integer'")
    ]


def test_yaml_errors_are_reported() -> None:
    report = validate_contract_text("doubleblind: [\n")

    assert [finding.rule for finding in report.findings] == ["yaml"]
    assert report.findings[0].path.startswith("line ")


def test_invalid_json_schema_in_a_data_schema_is_an_error() -> None:
    text = minimal_text("weight_g: { type: integer, minimum: 1 }", "weight_g: { type: integr }")

    report = validate_contract_text(text)

    assert [(finding.rule, finding.path) for finding in report.findings] == [
        ("schema", "schemas.CreateShipmentRequest.properties.weight_g.type")
    ]


def test_unknown_webhook_in_a_step_is_reported() -> None:
    text = minimal_text(
        "      - do: { party: consumer, action: pay_order }\n",
        "      - do: { party: consumer, action: pay_order }\n"
        "      - emit: { webhook: parcel_lost, body: {}, signature: valid,"
        " expect: { status: 200 } }\n",
    )

    assert findings_of(text) == [("rule 2", "error", "scenarios[0].steps[2].emit.webhook")]


def test_covers_naming_unknown_items_is_a_warning() -> None:
    text = minimal_text("covers: [D1, create_shipment]", "covers: [D1, D9, create_shipmnet]")

    assert findings_of(text) == [
        ("rule 5", "warning", "scenarios[0].covers[1]"),
        ("rule 5", "warning", "scenarios[0].covers[2]"),
    ]


def test_undeclared_secrets_are_reported() -> None:
    text = minimal_text("{{secrets.partner_api_key}}", "{{secrets.partner_key}}").replace(
        "secret: webhook_secret }", "secret: hook_secret }"
    )

    assert findings_of(text) == [
        ("rule 2", "error", "webhooks.status_changed.signature.secret"),
        ("rule 2", "error", "scenarios[1]"),
    ]


def test_actions_and_queries_are_checked_against_bindings() -> None:
    spokes = load_binding_text(
        SPOKES_BINDING.read_text(encoding="utf-8").replace("  order: {", "  get_order: {")
    )
    parcelly = load_binding_text(
        PARCELLY_BINDING.read_text(encoding="utf-8").replace("  reset: {", "  wipe: {")
    )

    report = validate_contract_file(SIGNED, [spokes, parcelly])

    messages = {finding.message for finding in report.findings}
    assert messages == {
        "query 'order' is not defined in the consumer binding",
        "action 'reset' is not defined in the provider binding",
    }
    assert "scenarios[5].steps[4].expect_state.query" in {f.path for f in report.findings}


def test_actions_are_not_checked_without_a_binding() -> None:
    text = minimal_text("action: pay_order", "action: pay_the_order")

    assert findings_of(text) == []


def test_party_must_match_the_scenario_side() -> None:
    text = minimal_text(
        "- do: { party: provider, action: reset }", "- do: { party: consumer, action: reset }"
    )

    assert findings_of(text) == [("rule 3", "error", "scenarios[1].steps[0].do.party")]


def test_e2e_scenarios_may_act_for_both_parties() -> None:
    text = minimal_text().replace("side: provider", "side: e2e")

    assert findings_of(text) == []


def test_duplicate_decision_ids_are_reported() -> None:
    text = minimal_text(
        '  - { id: D1, text: "The shop sends the weight in grams.", owner: consumer }\n',
        '  - { id: D1, text: "The shop sends the weight in grams.", owner: consumer }\n'
        '  - { id: D1, text: "Duplicate.", owner: provider }\n',
    )

    assert findings_of(text) == [("rule 4", "error", "decisions[1].id")]


def test_stub_status_must_be_listed_in_responses() -> None:
    text = minimal_text("default_status: 201", "default_status: 200")

    assert findings_of(text) == [
        ("rule 6", "error", "operations.create_shipment.stub.default_status")
    ]


def test_stub_templates_are_trusted_but_literal_values_are_checked() -> None:
    text = minimal_text('id: "shp_{{seq}}"', "id: 7")

    assert findings_of(text) == [("rule 6", "error", "operations.create_shipment.stub.body.id")]


def test_whole_template_values_in_a_stub_are_not_type_checked() -> None:
    text = minimal_text("price_gr: 1599", 'price_gr: "{{request.body.weight_g}}"')

    assert findings_of(text) == []


def test_partial_templates_are_filled_with_sample_text_and_checked() -> None:
    text = minimal_text("price_gr: 1599", 'price_gr: "gr-{{seq}}"')

    assert findings_of(text) == [
        ("rule 6", "error", "operations.create_shipment.stub.body.price_gr")
    ]


def test_finding_renders_and_serializes() -> None:
    finding = Finding("rule 2", "error", "a.b", "broken")

    assert finding.render() == "error   [rule 2] a.b: broken"
    assert Finding("yaml", "error", "", "empty").render() == "error   [yaml] empty"
    assert ValidationReport([finding]).to_dict()["findings"] == [
        {"rule": "rule 2", "severity": "error", "path": "a.b", "message": "broken"}
    ]


def test_report_serializes_with_counts() -> None:
    report = validate_contract_file(FIXTURES_DIR / "warning_rule5_uncovered_decision.yaml")

    assert report.to_dict()["ok"] is True
    assert report.to_dict()["warnings"] == 1
    assert report.to_dict()["errors"] == 0


@pytest.mark.parametrize(
    ("text", "rule"),
    [
        ("side: [\n", "yaml"),
        ('doubleblind_binding: "1.0"\nside: consumer\nbase_url: x\nactions: {}\n', "binding"),
    ],
)
def test_check_binding_text_reports_problems(text: str, rule: str) -> None:
    binding, report = check_binding_text(text)

    assert binding is None
    assert {finding.rule for finding in report.findings} == {rule}


def test_check_binding_text_returns_the_binding(tmp_path: Path) -> None:
    binding, report = check_binding_text(SPOKES_BINDING.read_text(encoding="utf-8"))

    assert report.findings == []
    assert binding is not None
    assert binding.side == "consumer"
