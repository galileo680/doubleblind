import re

import pytest

from doubleblind_contract.templating import (
    TemplateError,
    expression_root,
    is_whole_template,
    parse_expression,
    render,
    replace_templates,
    template_expressions,
    utc_now,
)

VARIABLES = {
    "ctx": {"order_id": "ORD-1001", "count": 3, "paid": True, "items": [{"sku": "CITY-U3"}]},
    "secrets": {"webhook_secret": "s3cret"},
    "args": {"weight_kg": 14.5, "to": "DELIVERED"},
    "request": {
        "body": {"reference": "ORD-7", "parcel": {"weight_g": 1500}},
        "headers": {"Idempotency-Key": "ORD-7"},
    },
    "stub": {"url": "http://127.0.0.1:9100"},
    "seq": 4,
}


def test_whole_template_keeps_the_type_of_the_value() -> None:
    assert render("{{ctx.order_id}}", VARIABLES) == "ORD-1001"
    assert render("{{ctx.count}}", VARIABLES) == 3
    assert render("{{ctx.paid}}", VARIABLES) is True
    assert render("{{args}}", VARIABLES) == {"weight_kg": 14.5, "to": "DELIVERED"}
    assert render("{{request.body.parcel.weight_g}}", VARIABLES) == 1500


def test_template_inside_text_becomes_text() -> None:
    assert render("Bearer {{secrets.webhook_secret}}", VARIABLES) == "Bearer s3cret"
    assert render("shp_{{seq}}", VARIABLES) == "shp_4"
    assert render("PCL{{seq}}PL-{{ctx.paid}}", VARIABLES) == "PCL4PL-true"
    assert render("{{stub.url}}/v1/shipments", VARIABLES) == "http://127.0.0.1:9100/v1/shipments"


def test_renders_nested_structures() -> None:
    value = {
        "reference": "{{ctx.order_id}}",
        "lines": ["{{ctx.items[0].sku}}", 7, None],
        "weight": "{{args.weight_kg}}",
    }

    assert render(value, VARIABLES) == {
        "reference": "ORD-1001",
        "lines": ["CITY-U3", 7, None],
        "weight": 14.5,
    }


def test_spaces_inside_braces_are_allowed() -> None:
    assert render("{{ ctx.order_id }}", VARIABLES) == "ORD-1001"


def test_header_names_are_looked_up_without_case() -> None:
    assert render("{{request.headers.idempotency-key}}", VARIABLES) == "ORD-7"


def test_text_without_templates_is_returned_unchanged() -> None:
    assert render("plain text {not a template}", VARIABLES) == "plain text {not a template}"
    assert render(12, VARIABLES) == 12


def test_now_and_uuid_are_generated() -> None:
    timestamp = render("{{now}}", {})
    identifier = render("{{uuid}}", {})

    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", timestamp)
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[0-9a-f]{4}-[0-9a-f]{12}", identifier
    )


def test_variables_take_precedence_over_functions() -> None:
    assert render("{{now}}", {"now": "fixed"}) == "fixed"


def test_unknown_variable_is_an_error() -> None:
    with pytest.raises(TemplateError, match="unknown variable 'missing'") as caught:
        render("{{missing.value}}", VARIABLES)

    assert caught.value.expression == "missing.value"


def test_missing_field_names_the_path_that_failed() -> None:
    with pytest.raises(TemplateError, match="'ctx' has no field 'shipment_id'"):
        render("{{ctx.shipment_id}}", VARIABLES)


def test_missing_list_item_is_an_error() -> None:
    with pytest.raises(TemplateError, match=r"'ctx.items' has no item \[3\]"):
        render("{{ctx.items[3].sku}}", VARIABLES)


def test_indexing_something_that_is_not_a_list_is_an_error() -> None:
    with pytest.raises(TemplateError, match=r"has no item \[0\]"):
        render("{{ctx.order_id[0]}}", VARIABLES)


def test_field_of_a_scalar_is_an_error() -> None:
    with pytest.raises(TemplateError, match=r"'ctx\.count' has no field 'value'"):
        render("{{ctx.count.value}}", VARIABLES)


def test_template_detection_helpers() -> None:
    assert is_whole_template("{{ctx.order_id}}")
    assert not is_whole_template("x{{ctx.order_id}}")
    assert expression_root("request.body.reference") == "request"
    assert parse_expression("ctx.items[0].sku") == ("ctx", ["items", 0, "sku"])


def test_parse_expression_rejects_text_that_is_not_an_expression() -> None:
    with pytest.raises(TemplateError, match="invalid expression"):
        parse_expression("1abc")


def test_lists_every_expression_in_a_structure() -> None:
    value = {"a": "{{ctx.x}} and {{secrets.y}}", "b": ["{{seq}}", 1], "c": None}

    assert template_expressions(value) == ["ctx.x", "secrets.y", "seq"]


def test_replace_templates_uses_the_given_replacement() -> None:
    assert replace_templates("shp_{{seq}}-{{uuid}}", lambda expression: expression.upper()) == (
        "shp_SEQ-UUID"
    )


def test_utc_now_ends_with_z() -> None:
    assert utc_now().endswith("Z")
