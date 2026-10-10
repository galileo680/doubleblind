from typing import Any

import pytest

from doubleblind_contract.matcher import (
    MISSING,
    Mismatch,
    json_type,
    match,
    matches,
    parse_path,
    resolve_path,
    values_equal,
)

REQUEST: dict[str, Any] = {
    "status": 201,
    "headers": {"Idempotency-Key": "ORD-1001", "Content-Type": "application/json"},
    "body": {
        "reference": "ORD-1001",
        "parcel": {"weight_g": 14500, "size": "B"},
        "receiver": {
            "phone": "+48600100200",
            "address": {"street": "Długa", "building": "5", "flat": "3", "city": "Warszawa"},
        },
        "items": [{"sku": "CITY-U3", "qty": 1}, {"sku": "BELL", "qty": 2}],
        "note": None,
        "paid": True,
        "price": 15.99,
    },
}


def test_matching_values_give_no_mismatches() -> None:
    expected = {
        "status": 201,
        "headers.Idempotency-Key": "ORD-1001",
        "body.reference": "ORD-1001",
        "body.parcel": {"weight_g": 14500, "size": "B"},
        "body.items[1].sku": "BELL",
    }

    assert match(REQUEST, expected) == []


def test_reports_path_expected_actual_and_reason() -> None:
    mismatches = match(REQUEST, {"body.parcel.weight_g": 14000})

    assert mismatches == [Mismatch("body.parcel.weight_g", 14000, 14500, "value mismatch")]
    assert mismatches[0].to_dict() == {
        "path": "body.parcel.weight_g",
        "expected": 14000,
        "actual": 14500,
        "message": "value mismatch",
    }


def test_reports_every_mismatch_not_only_the_first() -> None:
    mismatches = match(REQUEST, {"body.reference": "X", "status": 200})

    assert [mismatch.path for mismatch in mismatches] == ["body.reference", "status"]


def test_object_literal_is_a_subset_match() -> None:
    assert match(REQUEST, {"body.receiver.address": {"street": "Długa", "flat": "3"}}) == []

    mismatches = match(REQUEST, {"body.receiver.address": {"street": "al. Jana", "zip": "1"}})

    assert [(m.path, m.message) for m in mismatches] == [
        ("body.receiver.address.street", "value mismatch"),
        ("body.receiver.address.zip", "value is missing"),
    ]


def test_object_literal_needs_an_object() -> None:
    mismatches = match(REQUEST, {"body.reference": {"value": "ORD-1001"}})

    assert mismatches[0].message == "expected an object, got string"


def test_array_literal_is_compared_item_by_item() -> None:
    assert match(REQUEST, {"body.items": [{"sku": "CITY-U3"}, {"sku": "BELL"}]}) == []

    wrong_length = match(REQUEST, {"body.items": [{"sku": "CITY-U3"}]})
    wrong_item = match(REQUEST, {"body.items": [{"sku": "CITY-U3"}, {"sku": "LOCK"}]})
    not_array = match(REQUEST, {"body.reference": ["ORD-1001"]})

    assert wrong_length[0].message == "expected 1 items, got 2"
    assert wrong_item[0].path == "body.items[1].sku"
    assert not_array[0].message == "expected an array, got string"


def test_header_names_ignore_case() -> None:
    assert match(REQUEST, {"headers.idempotency-key": "ORD-1001"}) == []
    assert match(REQUEST, {"headers.CONTENT-TYPE": "application/json"}) == []


def test_body_field_names_keep_their_case() -> None:
    assert match(REQUEST, {"body.Reference": "ORD-1001"})[0].message == "value is missing"


def test_missing_paths_are_reported() -> None:
    assert match(REQUEST, {"body.tracking": "X"})[0].message == "value is missing"
    assert match(REQUEST, {"body.items[5].sku": "X"})[0].message == "value is missing"
    assert match(REQUEST, {"body.reference.x": "X"})[0].message == "value is missing"
    assert match(REQUEST, {"body.parcel[0]": "X"})[0].message == "value is missing"


def test_null_and_booleans_are_compared_exactly() -> None:
    assert match(REQUEST, {"body.note": None, "body.paid": True}) == []
    assert match(REQUEST, {"body.paid": 1})[0].message == "value mismatch"
    assert match({"count": 1}, {"count": True})[0].message == "value mismatch"


def test_numbers_compare_by_value() -> None:
    assert match({"weight": 14500.0}, {"weight": 14500}) == []
    assert match({"weight": "14500"}, {"weight": 14500})[0].message == "value mismatch"


def test_regex_operator() -> None:
    assert match(REQUEST, {"body.receiver.phone": {"$regex": r"^\+48\d{9}$"}}) == []
    assert "does not match" in match(REQUEST, {"body.reference": {"$regex": "^shp_"}})[0].message
    assert "expected text" in match(REQUEST, {"status": {"$regex": "2"}})[0].message
    assert (
        "invalid regular expression"
        in match(REQUEST, {"body.reference": {"$regex": "("}})[0].message
    )
    assert match(REQUEST, {"body.reference": {"$regex": 5}})[0].message == "$regex needs text"


@pytest.mark.parametrize(
    ("path", "type_name"),
    [
        ("body.reference", "string"),
        ("status", "integer"),
        ("status", "number"),
        ("body.price", "number"),
        ("body.paid", "boolean"),
        ("body.parcel", "object"),
        ("body.items", "array"),
        ("body.note", "null"),
    ],
)
def test_type_operator_accepts_matching_types(path: str, type_name: str) -> None:
    assert match(REQUEST, {path: {"$type": type_name}}) == []


def test_type_operator_rejects_other_types() -> None:
    assert match(REQUEST, {"body.paid": {"$type": "integer"}})[0].message == (
        "expected type integer, got boolean"
    )
    assert match(REQUEST, {"body.price": {"$type": "integer"}})[0].message == (
        "expected type integer, got number"
    )
    assert match(REQUEST, {"status": {"$type": "text"}})[0].message == "unknown type text"


def test_exists_operator() -> None:
    assert match(REQUEST, {"body.reference": {"$exists": True}}) == []
    assert match(REQUEST, {"body.tracking": {"$exists": False}}) == []
    assert match(REQUEST, {"body.note": {"$exists": True}}) == []
    assert match(REQUEST, {"body.tracking": {"$exists": True}})[0].message == "value is missing"
    assert match(REQUEST, {"body.reference": {"$exists": False}})[0].message == (
        "value should not exist"
    )


def test_bound_operators() -> None:
    assert match(REQUEST, {"body.parcel.weight_g": {"$gte": 14500, "$lte": 25000}}) == []
    assert match(REQUEST, {"body.parcel.weight_g": {"$gte": 15000}})[0].message == (
        "expected at least 15000"
    )
    assert match(REQUEST, {"body.parcel.weight_g": {"$lte": 5000}})[0].message == (
        "expected at most 5000"
    )
    assert match(REQUEST, {"body.reference": {"$gte": 1}})[0].message == (
        "expected a number, got string"
    )
    assert match(REQUEST, {"status": {"$gte": "1"}})[0].message == "$gte needs a number"


def test_in_operator() -> None:
    assert match(REQUEST, {"body.parcel.size": {"$in": ["A", "B", "C"]}}) == []
    assert match(REQUEST, {"body.parcel.size": {"$in": ["A", "C"]}})[0].message == (
        "value not allowed"
    )
    assert match(REQUEST, {"body.parcel.size": {"$in": "B"}})[0].message == "$in needs a list"


def test_contains_operator() -> None:
    assert match(REQUEST, {"body.items": {"$contains": {"sku": "BELL"}}}) == []
    assert match(REQUEST, {"body.items": {"$contains": {"sku": "LOCK"}}})[0].message == (
        "no item matches"
    )
    assert match(REQUEST, {"body.reference": {"$contains": "O"}})[0].message == (
        "expected an array for $contains, got string"
    )


def test_any_operator_accepts_any_existing_value() -> None:
    assert match(REQUEST, {"body.note": {"$any": True}, "body.parcel": {"$any": True}}) == []
    assert match(REQUEST, {"body.tracking": {"$any": True}})[0].message == "value is missing"


def test_operators_on_a_missing_value_report_it() -> None:
    assert match(REQUEST, {"body.tracking": {"$regex": "x"}})[0].message == "value is missing"


def test_unknown_operator_and_mixed_specs_are_reported() -> None:
    assert match(REQUEST, {"status": {"$between": [1, 2]}})[0].message == (
        "unknown operator $between"
    )
    assert (
        "cannot be mixed"
        in match(REQUEST, {"body.parcel": {"$exists": True, "size": "B"}})[0].message
    )


def test_operators_work_inside_object_literals() -> None:
    expected = {"body.parcel": {"weight_g": {"$gte": 1}, "size": {"$in": ["B"]}}}

    assert match(REQUEST, expected) == []


def test_helpers() -> None:
    assert parse_path("body.items[0].sku") == ["body", "items", 0, "sku"]
    assert resolve_path(REQUEST, "body.items[0].qty") == 1
    assert resolve_path(REQUEST, "body.nothing") is MISSING
    assert repr(MISSING) == "<missing>"
    assert Mismatch("a", 1, MISSING, "value is missing").to_dict()["actual"] is None
    assert matches({"a": 1}, {"a": 1})
    assert not matches({"a": 1}, {"a": 2})
    assert values_equal(1, 1.0)
    assert not values_equal(True, 1)
    assert not values_equal("1", 1)
    assert json_type(object()) == "object"
