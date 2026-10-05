from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

ThinkingToggle = Literal["chat_template_kwargs", "system_prompt", "none"]


class ThinkingCapability(BaseModel):
    toggle: ThinkingToggle = "chat_template_kwargs"
    reasoning_fields: list[str] = Field(default_factory=lambda: ["reasoning_content", "reasoning"])
    reasoning_budget_param: bool = True


class ModelCapabilities(BaseModel):
    alias: str
    id: str
    available: bool = True
    native_tools: bool = True
    json_schema: bool = True
    thinking: ThinkingCapability = Field(default_factory=ThinkingCapability)
    latency_ms: int | None = None
    errors: list[str] = Field(default_factory=list)


class CapabilityReport(BaseModel):
    generated_at: str
    base_url: str
    models: dict[str, ModelCapabilities]

    def for_alias(self, alias: str, model_id: str) -> ModelCapabilities:
        return self.models.get(alias) or ModelCapabilities(alias=alias, id=model_id)


def default_capabilities(alias: str, model_id: str) -> ModelCapabilities:
    return ModelCapabilities(alias=alias, id=model_id)


def load_capabilities(path: Path) -> CapabilityReport | None:
    if not path.is_file():
        return None
    return CapabilityReport.model_validate_json(path.read_text(encoding="utf-8"))


def save_capabilities(path: Path, report: CapabilityReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
