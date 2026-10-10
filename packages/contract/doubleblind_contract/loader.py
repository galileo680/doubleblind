import re
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from ruamel.yaml import YAML
from ruamel.yaml.error import YAMLError

from doubleblind_contract.models import STEP_KINDS, Binding, Contract

SCHEMA_REF_PREFIX = "#/schemas/"
ENV_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

PathParts = tuple[str | int, ...]


@dataclass(frozen=True)
class Problem:
    path: str
    message: str

    def render(self) -> str:
        return f"{self.path}: {self.message}" if self.path else self.message


class ContractLoadError(Exception):
    def __init__(self, problems: Sequence[Problem]) -> None:
        self.problems = list(problems)
        super().__init__("\n".join(problem.render() for problem in self.problems))


def format_path(parts: Sequence[str | int]) -> str:
    rendered = ""
    for part in parts:
        if isinstance(part, int):
            rendered += f"[{part}]"
        else:
            rendered += f".{part}" if rendered else part
    return rendered


def to_json_compatible(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {json_key(key): to_json_compatible(item) for key, item in value.items()}
    if isinstance(value, list):
        return [to_json_compatible(item) for item in value]
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    return value


def json_key(key: Any) -> str:
    if isinstance(key, datetime | date | time):
        return key.isoformat()
    return str(key)


def parse_yaml(text: str) -> dict[str, Any]:
    try:
        data = YAML(typ="safe").load(text)
    except YAMLError as error:
        raise ContractLoadError([yaml_problem(error)]) from error
    if not isinstance(data, Mapping):
        raise ContractLoadError([Problem("", "a contract must be a YAML mapping at the top level")])
    converted: dict[str, Any] = to_json_compatible(data)
    return converted


def yaml_problem(error: YAMLError) -> Problem:
    mark = getattr(error, "problem_mark", None)
    message = getattr(error, "problem", None) or str(error)
    location = f"line {mark.line + 1}, column {mark.column + 1}" if mark is not None else ""
    return Problem(location, message)


class RefResolver:
    def __init__(self, schemas: Mapping[str, Any]) -> None:
        self.schemas = schemas
        self.resolved: dict[str, Any] = {}
        self.in_progress: list[str] = []
        self.problems: list[Problem] = []

    def resolve_document(self, data: Mapping[str, Any]) -> dict[str, Any]:
        resolved_schemas = {name: self.schema(name) for name in self.schemas}
        return {
            key: resolved_schemas if key == "schemas" else self.walk(value, (key,))
            for key, value in data.items()
        }

    def schema(self, name: str) -> Any:
        if name not in self.resolved:
            self.in_progress.append(name)
            self.resolved[name] = self.walk(self.schemas[name], ("schemas", name))
            self.in_progress.pop()
        return self.resolved[name]

    def walk(self, value: Any, path: PathParts) -> Any:
        if isinstance(value, list):
            return [self.walk(item, (*path, index)) for index, item in enumerate(value)]
        if not isinstance(value, Mapping):
            return value
        siblings = {
            key: self.walk(item, (*path, key)) for key, item in value.items() if key != "$ref"
        }
        if "$ref" not in value:
            return siblings
        reference = value["$ref"]
        if not isinstance(reference, str):
            self.problems.append(
                Problem(format_path(path), f"$ref must be text like '{SCHEMA_REF_PREFIX}<Name>'")
            )
            return {**siblings, "$ref": reference}
        target = self.target(reference, path)
        if target is None:
            return {**siblings, "$ref": reference}
        if not siblings:
            return target
        return {**siblings, "allOf": [target, *siblings.get("allOf", [])]}

    def target(self, reference: str, path: PathParts) -> Any:
        location = format_path(path)
        if not reference.startswith(SCHEMA_REF_PREFIX):
            self.problems.append(
                Problem(
                    location,
                    f"unsupported $ref '{reference}': only '{SCHEMA_REF_PREFIX}<Name>' is allowed",
                )
            )
            return None
        name = reference.removeprefix(SCHEMA_REF_PREFIX)
        if name not in self.schemas:
            self.problems.append(
                Problem(location, f"$ref '{reference}' points to a schema that does not exist")
            )
            return None
        if name in self.in_progress:
            cycle = " -> ".join([*self.in_progress[self.in_progress.index(name) :], name])
            self.problems.append(Problem(location, f"$ref '{reference}' is circular: {cycle}"))
            return None
        return deepcopy(self.schema(name))


def resolve_refs(data: Mapping[str, Any]) -> dict[str, Any]:
    schemas = data.get("schemas")
    resolver = RefResolver(schemas if isinstance(schemas, Mapping) else {})
    resolved = resolver.resolve_document(data)
    if resolver.problems:
        raise ContractLoadError(resolver.problems)
    return resolved


def map_strings(value: Any, path: PathParts, change: Callable[[str, PathParts], str]) -> Any:
    if isinstance(value, Mapping):
        return {key: map_strings(item, (*path, key), change) for key, item in value.items()}
    if isinstance(value, list):
        return [map_strings(item, (*path, index), change) for index, item in enumerate(value)]
    if isinstance(value, str):
        return change(value, path)
    return value


def interpolate_env(data: Mapping[str, Any], env: Mapping[str, str]) -> dict[str, Any]:
    problems: list[Problem] = []

    def substitute(text: str, path: PathParts) -> str:
        def replace(found: re.Match[str]) -> str:
            name = found.group(1)
            if name in env:
                return env[name]
            problems.append(Problem(format_path(path), f"environment variable {name} is not set"))
            return found.group(0)

        return ENV_PLACEHOLDER.sub(replace, text)

    interpolated: dict[str, Any] = map_strings(data, (), substitute)
    if problems:
        raise ContractLoadError(problems)
    return interpolated


def readable_location(location: Sequence[str | int]) -> str:
    parts: list[str | int] = []
    for part in location:
        if parts and part == parts[-1] and part in STEP_KINDS:
            continue
        parts.append(part)
    return format_path(parts)


def structure_problems(error: ValidationError) -> list[Problem]:
    return [Problem(readable_location(detail["loc"]), detail["msg"]) for detail in error.errors()]


def build_contract(data: Mapping[str, Any]) -> Contract:
    try:
        return Contract.model_validate(data)
    except ValidationError as error:
        raise ContractLoadError(structure_problems(error)) from error


def build_binding(data: Mapping[str, Any]) -> Binding:
    try:
        return Binding.model_validate(data)
    except ValidationError as error:
        raise ContractLoadError(structure_problems(error)) from error


def load_contract_text(text: str, env: Mapping[str, str] | None = None) -> Contract:
    data = resolve_refs(parse_yaml(text))
    if env is not None:
        data = interpolate_env(data, env)
    return build_contract(data)


def load_contract(path: str | Path, env: Mapping[str, str] | None = None) -> Contract:
    return load_contract_text(Path(path).read_text(encoding="utf-8"), env)


def load_binding_text(text: str, env: Mapping[str, str] | None = None) -> Binding:
    data = parse_yaml(text)
    if env is not None:
        data = interpolate_env(data, env)
    return build_binding(data)


def load_binding(path: str | Path, env: Mapping[str, str] | None = None) -> Binding:
    return load_binding_text(Path(path).read_text(encoding="utf-8"), env)
