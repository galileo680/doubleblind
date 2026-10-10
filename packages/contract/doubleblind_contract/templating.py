import json
import re
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

EXPRESSION = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z0-9_-]+|\[\d+\])*"
TEMPLATE = re.compile(r"\{\{\s*(" + EXPRESSION + r")\s*\}\}")
ROOT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
SEGMENT = re.compile(r"\.([A-Za-z0-9_-]+)|\[(\d+)\]")

TemplateFunction = Callable[[], Any]


class TemplateError(Exception):
    def __init__(self, expression: str, reason: str) -> None:
        super().__init__(f"{{{{{expression}}}}}: {reason}")
        self.expression = expression
        self.reason = reason


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def new_uuid() -> str:
    return str(uuid.uuid4())


DEFAULT_FUNCTIONS: Mapping[str, TemplateFunction] = {"now": utc_now, "uuid": new_uuid}


def parse_expression(expression: str) -> tuple[str, list[str | int]]:
    root = ROOT.match(expression)
    if root is None:
        raise TemplateError(expression, "invalid expression")
    parts: list[str | int] = [
        found.group(1) if found.group(1) is not None else int(found.group(2))
        for found in SEGMENT.finditer(expression, root.end())
    ]
    return root.group(0), parts


def expression_root(expression: str) -> str:
    return parse_expression(expression)[0]


def is_whole_template(text: str) -> bool:
    return TEMPLATE.fullmatch(text) is not None


def contains_template(text: str) -> bool:
    return TEMPLATE.search(text) is not None


def template_expressions(value: Any) -> list[str]:
    if isinstance(value, str):
        return [found.group(1) for found in TEMPLATE.finditer(value)]
    if isinstance(value, Mapping):
        return [expression for item in value.values() for expression in template_expressions(item)]
    if isinstance(value, list):
        return [expression for item in value for expression in template_expressions(item)]
    return []


def replace_templates(text: str, replacement: Callable[[str], str]) -> str:
    return TEMPLATE.sub(lambda found: replacement(found.group(1)), text)


def as_text(value: Any) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def step_into(value: Any, part: str | int, expression: str, walked: str) -> Any:
    if isinstance(part, int):
        if isinstance(value, list) and part < len(value):
            return value[part]
        raise TemplateError(expression, f"'{walked}' has no item [{part}]")
    if isinstance(value, Mapping):
        if part in value:
            return value[part]
        lowered = part.lower()
        for key, item in value.items():
            if isinstance(key, str) and key.lower() == lowered:
                return item
    raise TemplateError(expression, f"'{walked}' has no field '{part}'")


class Renderer:
    def __init__(
        self, variables: Mapping[str, Any], functions: Mapping[str, TemplateFunction]
    ) -> None:
        self.variables = variables
        self.functions = functions
        self.computed: dict[str, Any] = {}

    def lookup(self, expression: str) -> Any:
        root, parts = parse_expression(expression)
        if root in self.variables:
            value = self.variables[root]
        elif not parts and root in self.functions:
            if root not in self.computed:
                self.computed[root] = self.functions[root]()
            return self.computed[root]
        else:
            raise TemplateError(expression, f"unknown variable '{root}'")
        walked = root
        for part in parts:
            value = step_into(value, part, expression, walked)
            walked += f"[{part}]" if isinstance(part, int) else f".{part}"
        return value

    def render(self, value: Any) -> Any:
        if isinstance(value, str):
            whole = TEMPLATE.fullmatch(value)
            if whole is not None:
                return self.lookup(whole.group(1))
            return replace_templates(value, lambda expression: as_text(self.lookup(expression)))
        if isinstance(value, Mapping):
            return {key: self.render(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.render(item) for item in value]
        return value


def render(
    value: Any,
    variables: Mapping[str, Any],
    functions: Mapping[str, TemplateFunction] | None = None,
) -> Any:
    return Renderer(variables, {**DEFAULT_FUNCTIONS, **(functions or {})}).render(value)
