import contextlib
import json
import re
from collections.abc import Iterator
from typing import Any

from jsonschema import Draft202012Validator

from doubleblind_llm.domain import ToolCall

REASONING_FIELDS = ("reasoning_content", "reasoning")
THINK_BLOCK = re.compile(r"<think>(.*?)</think>", re.DOTALL)
FENCED_JSON = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def split_reasoning(message: dict[str, Any]) -> tuple[str | None, str | None]:
    reasoning_parts = [
        str(message[field]).strip()
        for field in REASONING_FIELDS
        if isinstance(message.get(field), str) and message[field].strip()
    ]
    reasoning_parts = list(dict.fromkeys(reasoning_parts))
    content = message.get("content")
    if isinstance(content, str):
        reasoning_parts += [block.strip() for block in THINK_BLOCK.findall(content)]
        content = THINK_BLOCK.sub("", content)
        if "<think>" in content:
            content, _, unterminated = content.partition("<think>")
            reasoning_parts.append(unterminated.strip())
        content = content.strip() or None
    else:
        content = None
    reasoning = "\n\n".join(part for part in reasoning_parts if part) or None
    return content, reasoning


def iter_json_candidates(text: str) -> Iterator[Any]:
    for block in FENCED_JSON.findall(text):
        try:
            yield json.loads(block)
        except json.JSONDecodeError:
            continue
    with contextlib.suppress(json.JSONDecodeError):
        yield json.loads(text)
    decoder = json.JSONDecoder()
    for match in re.finditer(r"[{\[]", text):
        try:
            value, _ = decoder.raw_decode(text, match.start())
        except json.JSONDecodeError:
            continue
        yield value


def extract_json_value(text: str | None) -> Any | None:
    if not text:
        return None
    return next(iter_json_candidates(text), None)


def validate_against_schema(value: Any, schema: dict[str, Any]) -> list[str]:
    validator = Draft202012Validator(schema)
    return [
        f"{'/'.join(str(part) for part in error.absolute_path) or '<root>'}: {error.message}"
        for error in sorted(validator.iter_errors(value), key=lambda error: list(error.path))
    ]


def parse_structured(
    content: str | None, schema: dict[str, Any]
) -> tuple[dict[str, Any] | None, list[str]]:
    candidates = list(iter_json_candidates(content or ""))
    if not candidates:
        return None, ["response does not contain a JSON value"]
    first_errors: list[str] | None = None
    for candidate in candidates:
        errors = validate_against_schema(candidate, schema)
        if not errors and isinstance(candidate, dict):
            return candidate, []
        if first_errors is None:
            first_errors = errors or ["top-level JSON value must be an object"]
    return None, first_errors or []


def parse_native_tool_calls(raw_calls: list[dict[str, Any]]) -> list[ToolCall]:
    calls: list[ToolCall] = []
    for index, raw in enumerate(raw_calls):
        function = raw.get("function") or {}
        arguments = function.get("arguments") or "{}"
        if not isinstance(arguments, dict):
            parsed = extract_json_value(arguments)
            arguments = parsed if isinstance(parsed, dict) else {}
        calls.append(
            ToolCall(
                id=str(raw.get("id") or f"call_{index}"),
                name=str(function.get("name", "")),
                arguments=arguments,
            )
        )
    return calls


def parse_fallback_tool_calls(content: str | None, tool_names: set[str]) -> list[ToolCall]:
    for candidate in iter_json_candidates(content or ""):
        if not isinstance(candidate, dict) or candidate.get("tool") not in tool_names:
            continue
        arguments = candidate.get("args") or candidate.get("arguments") or {}
        if not isinstance(arguments, dict):
            arguments = {}
        return [ToolCall(id="fallback_0", name=str(candidate["tool"]), arguments=arguments)]
    return []


def fallback_tool_instructions(tools: list[dict[str, Any]]) -> str:
    described = [
        {
            "name": tool["function"]["name"],
            "description": tool["function"].get("description", ""),
            "parameters": tool["function"].get("parameters", {}),
        }
        for tool in tools
    ]
    return (
        "You can call tools. To call a tool, reply with ONLY a JSON object in a ```json code "
        'block shaped as {"tool": "<tool name>", "args": {...}}. Call at most one tool per '
        "reply. If no tool is needed, answer normally without JSON.\nAvailable tools:\n"
        + json.dumps(described, ensure_ascii=False, indent=2)
    )


def schema_instructions(schema: dict[str, Any]) -> str:
    return "Respond ONLY with a JSON object that matches this JSON Schema:\n" + json.dumps(
        schema, ensure_ascii=False, indent=2
    )
