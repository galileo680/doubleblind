import json
from pathlib import Path

import pytest
from contract_fixtures import (
    INVALID_MUTATIONS,
    SCHEMA_BROKEN,
    SCHEMA_PATH,
    STRUCTURALLY_VALID,
    load_yaml,
    minimal_contract,
    schema_errors,
)
from jsonschema import Draft202012Validator


def test_schema_file_is_a_valid_json_schema() -> None:
    Draft202012Validator.check_schema(json.loads(SCHEMA_PATH.read_text(encoding="utf-8")))


@pytest.mark.parametrize("path", STRUCTURALLY_VALID, ids=lambda path: path.name)
def test_structurally_valid_fixtures_pass_the_schema(path: Path) -> None:
    errors = schema_errors(load_yaml(path))

    assert [error.message for error in errors] == []


def test_schema_error_names_the_field_and_the_reason() -> None:
    errors = schema_errors(load_yaml(SCHEMA_BROKEN))

    assert len(errors) == 1
    assert list(errors[0].absolute_path) == ["version"]
    assert "is not of type 'integer'" in errors[0].message


@pytest.mark.parametrize("name", sorted(INVALID_MUTATIONS))
def test_schema_rejects_invalid_contracts(name: str) -> None:
    data = minimal_contract()
    INVALID_MUTATIONS[name](data)

    assert schema_errors(data) != []


def test_schema_rejects_an_unknown_step_kind_with_a_readable_message() -> None:
    data = minimal_contract()
    INVALID_MUTATIONS["unknown_step_kind"](data)

    messages = [error.message for error in schema_errors(data)]

    assert any("expect_recieved" in message for message in messages)
