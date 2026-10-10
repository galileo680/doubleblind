import json
from datetime import UTC, datetime
from typing import Any

import openai

from doubleblind_llm.capabilities import (
    CapabilityReport,
    ModelCapabilities,
    ThinkingCapability,
    ThinkingToggle,
)
from doubleblind_llm.client import LLMClient
from doubleblind_llm.domain import RawCompletion
from doubleblind_llm.parsing import parse_structured, split_reasoning
from doubleblind_llm.settings import ModelSpec

DOCTOR_ROLE = "doctor"
PING = [{"role": "user", "content": "Reply with exactly one word: pong"}]
ECHO_TOOL = {
    "type": "function",
    "function": {
        "name": "echo",
        "description": "Echo the given text back",
        "parameters": {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    },
}
PROBE_SCHEMA = {
    "type": "object",
    "properties": {"answer": {"type": "string"}},
    "required": ["answer"],
    "additionalProperties": False,
}
THINKING_OFF = {"chat_template_kwargs": {"enable_thinking": False}}


def has_reasoning(raw: RawCompletion) -> bool:
    _, reasoning = split_reasoning(raw.message)
    return reasoning is not None


def answered_pong(raw: RawCompletion) -> bool:
    content, _ = split_reasoning(raw.message)
    return content is not None and "pong" in content.lower()


class Doctor:
    def __init__(self, client: LLMClient) -> None:
        self.client = client
        self.errors: list[str] = []

    async def probe(
        self, model: ModelSpec, label: str, payload: dict[str, Any]
    ) -> RawCompletion | None:
        try:
            return await self.client.send(
                model,
                {"model": model.id, **payload},
                DOCTOR_ROLE,
                note=f"doctor:{label}",
                max_attempts=2,
            )
        except openai.BadRequestError as error:
            self.errors.append(f"{label}: rejected ({error.status_code})")
        except openai.APIError as error:
            self.errors.append(f"{label}: {type(error).__name__}")
        return None

    async def detect_thinking(self, model: ModelSpec) -> tuple[ThinkingToggle, int | None]:
        disabled = await self.probe(
            model,
            "thinking_off",
            {"messages": PING, "max_tokens": 64, "extra_body": THINKING_OFF},
        )
        if disabled and answered_pong(disabled) and not has_reasoning(disabled):
            return "chat_template_kwargs", disabled.latency_ms
        prompted = await self.probe(
            model,
            "thinking_off_prompt",
            {
                "messages": [{"role": "system", "content": "/no_think"}, *PING],
                "max_tokens": 64,
            },
        )
        if prompted and answered_pong(prompted) and not has_reasoning(prompted):
            return "system_prompt", prompted.latency_ms
        return "none", None

    async def detect_tools(self, model: ModelSpec, extra: dict[str, Any]) -> bool:
        raw = await self.probe(
            model,
            "native_tools",
            {
                "messages": [{"role": "user", "content": "Call the echo tool with text 'hi'."}],
                "tools": [ECHO_TOOL],
                "tool_choice": "auto",
                "max_tokens": 200,
                **extra,
            },
        )
        calls = (raw.message.get("tool_calls") or []) if raw else []
        return any(call.get("function", {}).get("name") == "echo" for call in calls)

    async def detect_json_schema(self, model: ModelSpec, extra: dict[str, Any]) -> bool:
        prompt = "Return JSON with field answer set to ok. Schema: " + json.dumps(PROBE_SCHEMA)
        raw = await self.probe(
            model,
            "json_schema",
            {
                "messages": [{"role": "user", "content": prompt}],
                "response_format": {
                    "type": "json_schema",
                    "json_schema": {"name": "probe", "schema": PROBE_SCHEMA, "strict": True},
                },
                "max_tokens": 100,
                **extra,
            },
        )
        if raw is None:
            return False
        content, _ = split_reasoning(raw.message)
        parsed, errors = parse_structured(content, PROBE_SCHEMA)
        return parsed is not None and not errors

    async def examine(self, model: ModelSpec, available: bool) -> ModelCapabilities:
        self.errors = []
        if not available:
            return ModelCapabilities(
                alias=model.alias,
                id=model.id,
                available=False,
                native_tools=False,
                json_schema=False,
                errors=["model is not listed by GET /v1/models"],
            )
        toggle, latency = await self.detect_thinking(model)
        extra = {"extra_body": THINKING_OFF} if toggle == "chat_template_kwargs" else {}
        native_tools = await self.detect_tools(model, extra)
        json_schema = await self.detect_json_schema(model, extra)
        return ModelCapabilities(
            alias=model.alias,
            id=model.id,
            available=True,
            native_tools=native_tools,
            json_schema=json_schema,
            thinking=ThinkingCapability(toggle=toggle),
            latency_ms=latency,
            errors=list(self.errors),
        )

    async def run(self) -> CapabilityReport:
        settings = self.client.settings
        listed = await self.client.list_model_ids()
        results = {
            alias: await self.examine(model, model.id in listed)
            for alias, model in settings.models.items()
        }
        return CapabilityReport(
            generated_at=datetime.now(UTC).isoformat(),
            base_url=settings.base_url,
            models=results,
        )
