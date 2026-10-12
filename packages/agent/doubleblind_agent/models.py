from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

BROKER_FIELDS = frozenset({"doubleblind", "id", "version", "status", "parties"})


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EntityField(Model):
    name: str
    type: str
    semantics: str
    unit: str | None
    format: str | None
    nullable: bool
    examples: list[str]


class Entity(Model):
    name: str
    fields: list[EntityField]


class ExposedApi(Model):
    method: str
    path: str
    purpose: str
    request_fields: list[str]
    errors: list[str]


class Hook(Model):
    name: str
    location: str
    how_to_use: str


class BusinessRule(Model):
    rule: str
    source: str
    public: bool


class DataQuirk(Model):
    field: str
    description: str


class IntegrationPoint(Model):
    description: str
    files: list[str]


class TestSetup(Model):
    how_to_run_tests: str
    how_to_start_app: str


class Psm(Model):
    system_summary: str
    role_in_integration: Literal["consumer", "provider"]
    entities: list[Entity]
    apis_exposed: list[ExposedApi]
    events_and_hooks: list[Hook]
    business_rules: list[BusinessRule]
    data_quirks: list[DataQuirk]
    integration_points: list[IntegrationPoint]
    test_setup: TestSetup


class DataItem(Model):
    concept: str
    representation: str
    examples: list[str]


class Brief(Model):
    party: str
    role: Literal["consumer", "provider"]
    goal: str
    data_we_have: list[DataItem]
    capabilities_offered: list[str]
    constraints: list[str]
    edge_cases: list[str]
    open_needs: list[str]


class Redaction(Model):
    text: str
    replacement: str


class ClassifierVerdict(Model):
    verdict: Literal["allow", "redact", "block"]
    reasons: list[str]
    redactions: list[Redaction]


class Patch(Model):
    op: Literal["add", "remove", "replace"]
    path: str
    value: Any = None


class HumanQuestion(Model):
    text: str
    options: list[str]
    default: str


class Citation(Model):
    decision_id: str
    source_url: str


class Turn(Model):
    message: str
    patches: list[Patch]
    questions_for_human: list[HumanQuestion]
    status: Literal["continue", "ready_to_sign"]

    def patch_list(self) -> list[dict[str, Any]]:
        return [
            patch.model_dump(exclude_none=patch.op == "remove")
            for patch in self.patches
            if patch.path.partition("/")[2].split("/")[0] not in BROKER_FIELDS
        ]


class TavilyTurn(Turn):
    lookup_standards: list[str] = Field(default_factory=list, max_length=3)
    citations: list[Citation] = Field(default_factory=list)


class TriageResult(Model):
    category: Literal["CODE_BUG", "CONTRACT_GAP", "COUNTERPARTY_ISSUE", "ENV_ERROR"]
    scenario_ids: list[str]
    summary_for_coder: str
    sanitized_reason_for_broker: str
    suggested_next_step: Literal["fix_code", "raise_contract_gap", "retry"]
