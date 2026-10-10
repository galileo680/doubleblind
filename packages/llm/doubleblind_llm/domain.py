from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from doubleblind_llm.settings import Mode, ThinkingSetting


class Role(StrEnum):
    PSM = "psm"
    BRIEF = "brief"
    NEGOTIATOR = "negotiator"
    CODER = "coder"
    TRIAGE = "triage"
    GUARD = "guard"
    REPAIR = "repair"
    HUMAN_SIM = "human_sim"


class ToolMode(StrEnum):
    NATIVE = "native"
    JSON_FALLBACK = "json_fallback"


class ToolCall(BaseModel):
    id: str
    name: str
    arguments: dict[str, Any]


class Usage(BaseModel):
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    estimated: bool = False


class LLMRequest(BaseModel):
    role: Role
    messages: list[dict[str, Any]]
    tools: list[dict[str, Any]] | None = None
    response_schema: dict[str, Any] | None = None
    thinking: ThinkingSetting | None = None
    reasoning_budget: int | None = Field(default=None, gt=0)
    temperature: float | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    session_id: str | None = None
    party: str | None = None

    @model_validator(mode="after")
    def tools_and_schema_are_exclusive(self) -> "LLMRequest":
        if self.tools and self.response_schema is not None:
            raise ValueError("A request cannot combine tools with a response schema")
        if not self.messages:
            raise ValueError("A request needs at least one message")
        return self


class RawCompletion(BaseModel):
    message: dict[str, Any]
    finish_reason: str | None
    usage: Usage
    model: str
    latency_ms: int
    cost_usd: float


class LLMResponse(BaseModel):
    content: str | None
    reasoning: str | None
    tool_calls: list[ToolCall]
    parsed: dict[str, Any] | None
    usage: Usage
    cost_usd: float
    model: str
    cache_key: str
    finish_reason: str | None
    latency_ms: int
    from_cache: bool = False
    repairs: int = 0


class LLMCallEvent(BaseModel):
    role: str
    model: str
    mode: Mode
    from_cache: bool
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    latency_ms: int
    session_id: str | None = None
    party: str | None = None
