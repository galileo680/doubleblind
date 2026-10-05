from typing import Any

import openai
import pytest
from llm_helpers import (
    ClientFactory,
    FakeTokenFactory,
    capability_report,
    completion_body,
    error_body,
)

from doubleblind_llm.domain import LLMCallEvent, LLMRequest, Role, ToolMode
from doubleblind_llm.errors import MissingApiKeyError, ReplayMissError, StructuredOutputError
from doubleblind_llm.settings import LLMSettings

ULTRA = "nvidia/Nemotron-3-Ultra-550b-a55b"
ECHO_TOOL = {
    "type": "function",
    "function": {
        "name": "echo",
        "description": "Echo text",
        "parameters": {"type": "object", "properties": {"text": {"type": "string"}}},
    },
}
ANSWER_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}


def ask(role: Role = Role.TRIAGE, **fields: Any) -> LLMRequest:
    return LLMRequest(role=role, messages=[{"role": "user", "content": "hi"}], **fields)


async def test_thinking_off_role_disables_thinking_in_chat_template(
    make_client: ClientFactory, api: FakeTokenFactory
) -> None:
    api.reply(completion_body("pong"))
    response = await make_client().complete(ask(Role.TRIAGE))
    body = api.chat_bodies[-1]
    assert body["model"] == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    assert body["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_budget" not in body
    assert body["temperature"] == 0
    assert response.content == "pong"


async def test_thinking_on_role_sends_reasoning_budget(
    make_client: ClientFactory, api: FakeTokenFactory
) -> None:
    api.reply(completion_body("deal", model=ULTRA))
    await make_client().complete(ask(Role.NEGOTIATOR))
    body = api.chat_bodies[-1]
    assert body["model"] == ULTRA
    assert body["chat_template_kwargs"] == {"enable_thinking": True}
    assert body["reasoning_budget"] == 6000
    assert body["max_tokens"] == 8000


async def test_system_prompt_toggle_is_used_when_chat_template_is_unsupported(
    make_client: ClientFactory, api: FakeTokenFactory, settings: LLMSettings
) -> None:
    api.reply(completion_body("pong"))
    capabilities = capability_report(settings, toggle="system_prompt")
    await make_client(capabilities=capabilities).complete(ask(Role.TRIAGE))
    body = api.chat_bodies[-1]
    assert "chat_template_kwargs" not in body
    assert body["messages"][0] == {"role": "system", "content": "/no_think"}


@pytest.mark.parametrize(
    "fields",
    [
        {"content": "\n\nanswer", "reasoning": "step"},
        {"content": "answer", "reasoning_content": "step"},
        {"content": "<think>step</think>\nanswer"},
    ],
)
async def test_reasoning_is_separated_from_content(
    make_client: ClientFactory, api: FakeTokenFactory, fields: dict[str, str]
) -> None:
    content = fields.pop("content")
    api.reply(completion_body(content, **fields))
    response = await make_client().complete(ask())
    assert response.content == "answer"
    assert response.reasoning == "step"


async def test_native_tool_calls_are_parsed(
    make_client: ClientFactory, api: FakeTokenFactory
) -> None:
    tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "echo", "arguments": '{"text": "hi"}'},
        }
    ]
    api.reply(completion_body(None, tool_calls=tool_calls, finish_reason="tool_calls"))
    response = await make_client().complete(ask(Role.CODER, tools=[ECHO_TOOL]))
    body = api.chat_bodies[-1]
    assert body["tools"] == [ECHO_TOOL]
    assert body["tool_choice"] == "auto"
    assert response.tool_mode == ToolMode.NATIVE
    call = response.tool_calls[0]
    assert (call.id, call.name, call.arguments) == ("call_1", "echo", {"text": "hi"})
    assert response.assistant_message()["tool_calls"][0]["function"]["name"] == "echo"
    assert response.tool_result_message(call, "hi") == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "hi",
    }


async def test_json_fallback_tool_calls_are_parsed_from_content(
    make_client: ClientFactory, api: FakeTokenFactory, settings: LLMSettings
) -> None:
    api.reply(completion_body('Sure.\n```json\n{"tool": "echo", "args": {"text": "hi"}}\n```'))
    capabilities = capability_report(settings, native_tools=False)
    response = await make_client(capabilities=capabilities).complete(
        ask(Role.CODER, tools=[ECHO_TOOL])
    )
    body = api.chat_bodies[-1]
    assert "tools" not in body
    assert "Available tools" in body["messages"][0]["content"]
    assert response.tool_mode == ToolMode.JSON_FALLBACK
    call = response.tool_calls[0]
    assert (call.name, call.arguments) == ("echo", {"text": "hi"})
    assert response.tool_result_message(call, {"ok": True})["role"] == "user"


async def test_structured_output_uses_json_schema(
    make_client: ClientFactory, api: FakeTokenFactory
) -> None:
    api.reply(completion_body('{"answer": "ok"}'))
    response = await make_client().complete(ask(response_schema=ANSWER_SCHEMA))
    body = api.chat_bodies[-1]
    assert body["response_format"]["type"] == "json_schema"
    assert body["response_format"]["json_schema"]["schema"] == ANSWER_SCHEMA
    assert "JSON Schema" in body["messages"][0]["content"]
    assert response.parsed == {"answer": "ok"}
    assert response.repairs == 0


async def test_structured_output_falls_back_to_json_object(
    make_client: ClientFactory, api: FakeTokenFactory, settings: LLMSettings
) -> None:
    api.reply(completion_body('{"answer": "ok"}'))
    capabilities = capability_report(settings, json_schema=False)
    await make_client(capabilities=capabilities).complete(ask(response_schema=ANSWER_SCHEMA))
    assert api.chat_bodies[-1]["response_format"] == {"type": "json_object"}


async def test_invalid_structured_output_is_repaired(
    make_client: ClientFactory, api: FakeTokenFactory
) -> None:
    api.reply_sequence(
        (200, completion_body('{"answer": 3}')),
        (200, completion_body('{"answer": "fixed"}')),
    )
    response = await make_client().complete(ask(Role.BRIEF, response_schema=ANSWER_SCHEMA))
    assert response.parsed == {"answer": "fixed"}
    assert response.repairs == 1
    repair_body = api.chat_bodies[1]
    assert repair_body["model"] == "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"
    assert "Validation errors" in repair_body["messages"][-1]["content"]


async def test_unrepairable_output_raises(
    make_client: ClientFactory, api: FakeTokenFactory
) -> None:
    api.reply(completion_body("not json at all"))
    with pytest.raises(StructuredOutputError):
        await make_client().complete(ask(response_schema=ANSWER_SCHEMA))
    assert api.chat_calls == 3


async def test_rate_limit_is_retried(make_client: ClientFactory, api: FakeTokenFactory) -> None:
    api.reply_sequence((429, error_body("slow down")), (200, completion_body("pong")))
    response = await make_client().complete(ask())
    assert response.content == "pong"
    assert api.chat_calls == 2


async def test_server_error_is_retried_until_attempts_run_out(
    make_client: ClientFactory, api: FakeTokenFactory
) -> None:
    api.reply(error_body("boom"), status=503)
    with pytest.raises(openai.InternalServerError):
        await make_client().complete(ask())
    assert api.chat_calls == 5


async def test_bad_request_is_not_retried(
    make_client: ClientFactory, api: FakeTokenFactory
) -> None:
    api.reply(error_body("bad"), status=400)
    with pytest.raises(openai.BadRequestError):
        await make_client().complete(ask())
    assert api.chat_calls == 1


async def test_cost_is_computed_ledgered_and_emitted(
    make_client: ClientFactory, api: FakeTokenFactory
) -> None:
    api.reply(completion_body("ok", prompt_tokens=1000, completion_tokens=500, model=ULTRA))
    events: list[LLMCallEvent] = []
    client = make_client(on_call=events.append)
    response = await client.complete(ask(Role.NEGOTIATOR, session_id="s1", party="spokes"))
    assert response.cost_usd == pytest.approx(0.0025)
    assert client.ledger is not None
    assert client.ledger.total_usd() == pytest.approx(0.0025)
    assert client.ledger.entries()[0].party == "spokes"
    assert len(events) == 1
    assert events[0].role == "negotiator"
    assert events[0].from_cache is False


async def test_record_then_replay_without_network(
    make_client: ClientFactory, api: FakeTokenFactory
) -> None:
    request = ask(Role.NEGOTIATOR)
    api.reply(completion_body("deal", reasoning_content="why", model=ULTRA))
    recorded = await make_client("record").complete(request)
    calls_after_recording = len(api.requests)
    events: list[LLMCallEvent] = []
    replay_client = make_client("replay", api_key=None, on_call=events.append)
    replayed = await replay_client.complete(request)
    assert len(api.requests) == calls_after_recording
    assert replayed.from_cache is True
    assert replayed.model_dump(exclude={"from_cache"}) == recorded.model_dump(
        exclude={"from_cache"}
    )
    assert events[0].from_cache is True
    assert replay_client.ledger is not None
    assert len(replay_client.ledger.entries()) == 1


async def test_replay_miss_raises(make_client: ClientFactory) -> None:
    with pytest.raises(ReplayMissError):
        await make_client("replay").complete(ask())


async def test_live_call_without_api_key_raises(
    make_client: ClientFactory, api: FakeTokenFactory
) -> None:
    with pytest.raises(MissingApiKeyError):
        await make_client(api_key=None).complete(ask())
    assert api.chat_calls == 0
