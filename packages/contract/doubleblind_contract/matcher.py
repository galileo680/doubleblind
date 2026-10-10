import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PATH_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d+)\]")
OPERATORS = frozenset({"$regex", "$type", "$exists", "$gte", "$lte", "$in", "$contains", "$any"})
JSON_TYPES = frozenset({"string", "integer", "number", "boolean", "object", "array", "null"})


class Missing:
    def __repr__(self) -> str:
        return "<missing>"


MISSING = Missing()


@dataclass(frozen=True)
class Mismatch:
    path: str
    expected: Any
    actual: Any
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "expected": self.expected,
            "actual": None if self.actual is MISSING else self.actual,
            "message": self.message,
        }


def parse_path(path: str) -> list[str | int]:
    return [
        found.group(1) if found.group(1) is not None else int(found.group(2))
        for found in PATH_TOKEN.finditer(path)
    ]


def lookup_key(container: Mapping[str, Any], key: str, ignore_case: bool) -> Any:
    if key in container:
        return container[key]
    if ignore_case:
        lowered = key.lower()
        for name, value in container.items():
            if isinstance(name, str) and name.lower() == lowered:
                return value
    return MISSING


def resolve_path(data: Any, path: str) -> Any:
    current = data
    parts = parse_path(path)
    for index, part in enumerate(parts):
        if isinstance(part, int):
            if not isinstance(current, list) or part >= len(current):
                return MISSING
            current = current[part]
            continue
        if not isinstance(current, Mapping):
            return MISSING
        inside_headers = index == 1 and parts[0] == "headers"
        current = lookup_key(current, part, ignore_case=inside_headers)
        if current is MISSING:
            return MISSING
    return current


def json_type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, Mapping):
        return "object"
    return type(value).__name__


def is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


def values_equal(actual: Any, expected: Any) -> bool:
    if isinstance(actual, bool) or isinstance(expected, bool):
        return type(actual) is type(expected) and actual == expected
    if is_number(actual) and is_number(expected):
        return bool(actual == expected)
    return type(actual) is type(expected) and bool(actual == expected)


def child_path(path: str, part: str | int) -> str:
    if isinstance(part, int):
        return f"{path}[{part}]"
    return f"{path}.{part}" if path else part


def match(actual: Any, expected: Mapping[str, Any]) -> list[Mismatch]:
    mismatches: list[Mismatch] = []
    for path, expectation in expected.items():
        mismatches.extend(compare(resolve_path(actual, path), expectation, path))
    return mismatches


def matches(actual: Any, expectation: Any) -> bool:
    return not compare(actual, expectation, "")


def compare(actual: Any, expected: Any, path: str) -> list[Mismatch]:
    if isinstance(expected, Mapping) and any(str(key).startswith("$") for key in expected):
        return compare_operators(actual, expected, path)
    if actual is MISSING:
        return [Mismatch(path, expected, actual, "value is missing")]
    if isinstance(expected, Mapping):
        if not isinstance(actual, Mapping):
            message = f"expected an object, got {json_type(actual)}"
            return [Mismatch(path, expected, actual, message)]
        mismatches: list[Mismatch] = []
        for key, item in expected.items():
            mismatches.extend(compare(actual.get(key, MISSING), item, child_path(path, key)))
        return mismatches
    if isinstance(expected, list):
        if not isinstance(actual, list):
            return [Mismatch(path, expected, actual, f"expected an array, got {json_type(actual)}")]
        if len(actual) != len(expected):
            return [
                Mismatch(
                    path, expected, actual, f"expected {len(expected)} items, got {len(actual)}"
                )
            ]
        mismatches = []
        for index, (actual_item, expected_item) in enumerate(zip(actual, expected, strict=True)):
            mismatches.extend(compare(actual_item, expected_item, child_path(path, index)))
        return mismatches
    if values_equal(actual, expected):
        return []
    return [Mismatch(path, expected, actual, "value mismatch")]


def compare_operators(actual: Any, spec: Mapping[str, Any], path: str) -> list[Mismatch]:
    fields = [key for key in spec if not str(key).startswith("$")]
    if fields:
        return [Mismatch(path, spec, actual, f"operators cannot be mixed with fields: {fields}")]
    mismatches: list[Mismatch] = []
    for operator, argument in spec.items():
        failure = check_operator(operator, argument, actual)
        if failure is not None:
            mismatches.append(Mismatch(path, {operator: argument}, actual, failure))
    return mismatches


def check_operator(operator: str, argument: Any, actual: Any) -> str | None:
    if operator not in OPERATORS:
        return f"unknown operator {operator}"
    if operator == "$exists":
        exists = actual is not MISSING
        if exists == bool(argument):
            return None
        return "value is missing" if argument else "value should not exist"
    if actual is MISSING:
        return "value is missing"
    if operator == "$any":
        return None
    if operator == "$regex":
        return check_regex(argument, actual)
    if operator == "$type":
        return check_type(argument, actual)
    if operator in ("$gte", "$lte"):
        return check_bound(operator, argument, actual)
    if operator == "$in":
        if not isinstance(argument, list):
            return "$in needs a list"
        return None if any(matches(actual, option) for option in argument) else "value not allowed"
    if not isinstance(actual, list):
        return f"expected an array for $contains, got {json_type(actual)}"
    if any(matches(item, argument) for item in actual):
        return None
    return "no item matches"


def check_regex(pattern: Any, actual: Any) -> str | None:
    if not isinstance(pattern, str):
        return "$regex needs text"
    if not isinstance(actual, str):
        return f"expected text for $regex, got {json_type(actual)}"
    try:
        found = re.search(pattern, actual)
    except re.error as error:
        return f"invalid regular expression: {error}"
    return None if found else f"does not match /{pattern}/"


def check_type(name: Any, actual: Any) -> str | None:
    if name not in JSON_TYPES:
        return f"unknown type {name}"
    if name == "number" and is_number(actual):
        return None
    if json_type(actual) == name:
        return None
    return f"expected type {name}, got {json_type(actual)}"


def check_bound(operator: str, bound: Any, actual: Any) -> str | None:
    if not is_number(bound):
        return f"{operator} needs a number"
    if not is_number(actual):
        return f"expected a number, got {json_type(actual)}"
    if operator == "$gte":
        return None if actual >= bound else f"expected at least {bound}"
    return None if actual <= bound else f"expected at most {bound}"
