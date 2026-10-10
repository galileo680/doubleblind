import json
from functools import cache
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
CONTRACT_SCHEMA_FILE = "contract.schema.json"
BINDING_SCHEMA_FILE = "binding.schema.json"


@cache
def load_schema(file_name: str) -> dict[str, Any]:
    schema: dict[str, Any] = json.loads((SCHEMA_DIR / file_name).read_text(encoding="utf-8"))
    return schema


@cache
def contract_validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema(CONTRACT_SCHEMA_FILE))


@cache
def binding_validator() -> Draft202012Validator:
    return Draft202012Validator(load_schema(BINDING_SCHEMA_FILE))


@cache
def meta_validator() -> Draft202012Validator:
    return Draft202012Validator(Draft202012Validator.META_SCHEMA)
