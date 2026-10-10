import pytest

from doubleblind_llm.domain import Usage
from doubleblind_llm.parsing import (
    extract_json_value,
    parse_fallback_tool_calls,
    parse_native_tool_calls,
    parse_structured,
    split_reasoning,
)
from doubleblind_llm.pricing import cost_usd, estimate_prompt_tokens
from doubleblind_llm.settings import Price

SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
}


@pytest.mark.parametrize(
    ("message", "content", "reasoning"),
    [
        ({"content": "\npong", "reasoning": "think"}, "pong", "think"),
        ({"content": "pong", "reasoning_content": "think"}, "pong", "think"),
        ({"content": "pong", "reasoning": "same", "reasoning_content": "same"}, "pong", "same"),
        ({"content": "<think>a</think>pong"}, "pong", "a"),
        ({"content": "pong<think>cut off"}, "pong", "cut off"),
        ({"content": "pong"}, "pong", None),
        ({"content": None}, None, None),
        ({"content": "   "}, None, None),
    ],
)
def test_split_reasoning(
    message: dict[str, str], content: str | None, reasoning: str | None
) -> None:
    assert split_reasoning(message) == (content, reasoning)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('```json\n{"a": 1}\n```', {"a": 1}),
        ('{"a": 1}', {"a": 1}),
        ('Here you go: {"a": 1} done', {"a": 1}),
        ("[1, 2]", [1, 2]),
        ("no json", None),
        (None, None),
    ],
)
def test_extract_json_value(text: str | None, expected: object) -> None:
    assert extract_json_value(text) == expected


def test_parse_structured_reports_validation_errors() -> None:
    parsed, errors = parse_structured('{"answer": 3}', SCHEMA)
    assert parsed is None
    assert errors == ["answer: 3 is not of type 'string'"]


def test_parse_structured_skips_invalid_candidates() -> None:
    parsed, errors = parse_structured('{"x": 1} then {"answer": "ok"}', SCHEMA)
    assert parsed == {"answer": "ok"}
    assert errors == []


def test_parse_structured_without_json() -> None:
    assert parse_structured("plain text", SCHEMA) == (
        None,
        ["response does not contain a JSON value"],
    )


def test_native_tool_calls_tolerate_invalid_arguments() -> None:
    calls = parse_native_tool_calls(
        [{"function": {"name": "echo", "arguments": "not json"}}, {"function": {"name": "x"}}]
    )
    assert [(call.id, call.name, call.arguments) for call in calls] == [
        ("call_0", "echo", {}),
        ("call_1", "x", {}),
    ]


def test_fallback_tool_calls_ignore_unknown_tools() -> None:
    assert parse_fallback_tool_calls('{"tool": "rm", "args": {}}', {"echo"}) == []
    calls = parse_fallback_tool_calls('{"tool": "echo", "arguments": {"text": "x"}}', {"echo"})
    assert calls[0].arguments == {"text": "x"}


def test_cost_and_estimates() -> None:
    price = Price(input_per_m=1.0, output_per_m=3.0)
    assert cost_usd(price, Usage(prompt_tokens=1_000_000, completion_tokens=0)) == 1.0
    assert estimate_prompt_tokens({"messages": [], "max_tokens": 10_000}) == 5
