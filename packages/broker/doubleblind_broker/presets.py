from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from doubleblind_contract.guard_rules import SyntheticAllowlist
from doubleblind_contract.loader import parse_yaml


class PresetModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PresetParty(PresetModel):
    party: str = Field(pattern=r"^[a-z][a-z0-9_-]*$")
    display_name: str
    workspace: str | None = None


class AllowlistSpec(PresetModel):
    phones: list[str] = Field(default_factory=list)
    emails: list[str] = Field(default_factory=list)


class AutopilotAnswer(PresetModel):
    match: list[str]
    answer: str


class Personas(PresetModel):
    consumer: str = "business owner on the consumer side"
    provider: str = "partner manager on the provider side"


class Preset(PresetModel):
    name: str
    contract_id: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]*$")
    consumer: PresetParty
    provider: PresetParty
    synthetic_allowlist: AllowlistSpec = Field(default_factory=AllowlistSpec)
    autopilot_answers: list[AutopilotAnswer] = Field(default_factory=list)
    personas: Personas = Field(default_factory=Personas)
    scope: str | None = None

    def allowlist(self) -> SyntheticAllowlist:
        return SyntheticAllowlist.build(
            self.synthetic_allowlist.phones, self.synthetic_allowlist.emails
        )

    def preset_answer(self, question: str) -> str | None:
        lowered = question.lower()
        for rule in self.autopilot_answers:
            if any(keyword.lower() in lowered for keyword in rule.match):
                return rule.answer
        return None


class PresetNotFoundError(Exception):
    pass


def load_preset(presets_dir: Path, name: str) -> Preset:
    path = presets_dir / f"{name}.yaml"
    if not path.is_file():
        raise PresetNotFoundError(f"preset '{name}' not found in {presets_dir}")
    return Preset.model_validate(parse_yaml(path.read_text(encoding="utf-8")))
