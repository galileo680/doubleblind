import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2

from doubleblind_llm.capabilities import CapabilityReport, ModelCapabilities, ThinkingCapability
from doubleblind_llm.client import LLMClient
from doubleblind_llm.settings import LLMSettings

ROOT = Path(__file__).resolve().parents[3]
NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"

Reply = tuple[int, dict[str, Any]]
ChatHandler = Callable[[dict[str, Any]], Reply]
ClientFactory = Callable[..., LLMClient]


def completion_body(
    content: str | None = "ok",
    *,
    reasoning: str | None = None,
    reasoning_content: str | None = None,
    tool_calls: list[dict[str, Any]] | None = None,
    prompt_tokens: int = 10,
    completion_tokens: int = 5,
    finish_reason: str = "stop",
    model: str = NANO,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if reasoning is not None:
        message["reasoning"] = reasoning
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 0,
        "model": model,
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def error_body(message: str) -> dict[str, Any]:
    return {"error": {"message": message}}


def capability_report(
    settings: LLMSettings,
    *,
    native_tools: bool = True,
    json_schema: bool = True,
    toggle: str = "chat_template_kwargs",
) -> CapabilityReport:
    return CapabilityReport(
        generated_at="2026-10-05T00:00:00+00:00",
        base_url=settings.base_url,
        models={
            alias: ModelCapabilities(
                alias=alias,
                id=spec.id,
                native_tools=native_tools,
                json_schema=json_schema,
                thinking=ThinkingCapability.model_validate({"toggle": toggle}),
            )
            for alias, spec in settings.models.items()
        },
    )


class FakeTokenFactory:
    def __init__(self) -> None:
        self.chat_bodies: list[dict[str, Any]] = []
        self.requests: list[httpx2.Request] = []
        self.model_ids: list[str] = []
        self.handler: ChatHandler | None = None
        self._queue: list[Reply] = []
        self._default: Reply | None = None

    def reply(self, body: dict[str, Any], status: int = 200) -> None:
        self._default = (status, body)

    def reply_sequence(self, *replies: Reply) -> None:
        self._queue.extend(replies)

    def client(self) -> httpx2.AsyncClient:
        return httpx2.AsyncClient(transport=httpx2.MockTransport(self._handle))

    @property
    def chat_calls(self) -> int:
        return len(self.chat_bodies)

    def _handle(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/models"):
            data = [{"id": item, "object": "model"} for item in self.model_ids]
            return httpx2.Response(200, json={"object": "list", "data": data})
        if not path.endswith("/chat/completions"):
            return httpx2.Response(404, json=error_body(f"unexpected path {path}"))
        body = json.loads(request.content)
        self.chat_bodies.append(body)
        if self._queue:
            status, payload = self._queue.pop(0)
        elif self.handler is not None:
            status, payload = self.handler(body)
        elif self._default is not None:
            status, payload = self._default
        else:
            status, payload = 500, error_body("no reply configured")
        return httpx2.Response(status, json=payload)
