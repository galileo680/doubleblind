from typing import Annotated, Any, ClassVar, Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag,
    model_validator,
)

Slug = Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]*$")]
SnakeId = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
UpperId = Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Za-z0-9_-]*$")]
SchemaName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]*$")]
HeaderName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9-]*$")]
StatusCodeKey = Annotated[str, StringConstraints(pattern=r"^[1-5][0-9]{2}$")]
RequestPath = Annotated[str, StringConstraints(pattern=r"^/")]
JsonPath = Annotated[str, StringConstraints(pattern=r"^\$")]
SignatureFormat = Annotated[str, StringConstraints(pattern=r"\{hex\}")]
NonEmptyText = Annotated[str, StringConstraints(min_length=1)]
HttpStatus = Annotated[int, Field(ge=100, le=599)]
Count = Annotated[int, Field(ge=0)]
DistinctCount = Annotated[int, Field(ge=1)]
Seconds = Annotated[float, Field(gt=0)]
DataSchema = dict[str, Any] | bool
MatchSpec = dict[str, Any]

PartyRole = Literal["consumer", "provider"]
Side = Literal["consumer", "provider", "e2e"]
HttpMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]
ContractStatus = Literal["draft", "proposed", "signed"]
EmitSignature = Literal["valid", "invalid", "missing"]
StepKind = Literal[
    "do",
    "send",
    "emit",
    "expect_received",
    "expect_no_request",
    "expect_emitted",
    "expect_state",
]

STEP_KINDS: tuple[StepKind, ...] = (
    "do",
    "send",
    "emit",
    "expect_received",
    "expect_no_request",
    "expect_emitted",
    "expect_state",
)


class ContractModel(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)


class Party(ContractModel):
    id: Slug
    name: NonEmptyText


class Parties(ContractModel):
    consumer: Party
    provider: Party


class Secret(ContractModel):
    name: SnakeId
    description: str | None = None


class HeaderRule(ContractModel):
    required: bool = False
    pattern: str | None = None


class OperationRequest(ContractModel):
    method: HttpMethod
    path: RequestPath
    headers: dict[HeaderName, HeaderRule] = Field(default_factory=dict)
    body: DataSchema | None = None


class ResponseSpec(ContractModel):
    body: DataSchema | None = None


class StubSpec(ContractModel):
    default_status: HttpStatus
    body: Any = None
    idempotent_on_header: HeaderName | None = None


class Operation(ContractModel):
    description: str | None = None
    direction: Literal["consumer_to_provider"]
    request: OperationRequest
    responses: Annotated[dict[StatusCodeKey, ResponseSpec], Field(min_length=1)]
    stub: StubSpec | None = None


class WebhookRequest(ContractModel):
    method: HttpMethod
    url: NonEmptyText
    headers: dict[HeaderName, HeaderRule] = Field(default_factory=dict)
    body: DataSchema | None = None


class WebhookSignature(ContractModel):
    algorithm: Literal["hmac-sha256"]
    header: HeaderName
    format: SignatureFormat
    over: Literal["raw_body"]
    secret: SnakeId


class ConsumerResponses(ContractModel):
    valid_signature: HttpStatus
    invalid_signature: HttpStatus


class Webhook(ContractModel):
    description: str | None = None
    direction: Literal["provider_to_consumer"]
    request: WebhookRequest
    signature: WebhookSignature | None = None
    consumer_responses: ConsumerResponses | None = None


class Decision(ContractModel):
    id: UpperId
    text: NonEmptyText
    owner: PartyRole
    origin_question: UpperId | None = None
    approved_by: list[Slug] = Field(default_factory=list)


class DoAction(ContractModel):
    party: PartyRole
    action: SnakeId
    args: dict[str, Any] | None = None


class SendExpectation(ContractModel):
    status: HttpStatus
    match: MatchSpec | None = None


class SendRequest(ContractModel):
    operation: SnakeId
    headers: dict[str, str] | None = None
    body: Any = None
    expect: SendExpectation
    capture: dict[SnakeId, JsonPath] | None = None


class EmitExpectation(ContractModel):
    status: HttpStatus


class EmitWebhook(ContractModel):
    webhook: SnakeId
    body: Any
    signature: EmitSignature
    expect: EmitExpectation


class ExpectReceived(ContractModel):
    operation: SnakeId
    count: Count | None = None
    distinct: dict[str, DistinctCount] | None = None
    match: MatchSpec | None = None
    within_s: Seconds | None = None


class ExpectNoRequest(ContractModel):
    operation: SnakeId | None = None
    within_s: Seconds


class ExpectEmitted(ContractModel):
    webhook: SnakeId
    count: Count | None = None
    signature: Literal["valid"] | None = None
    match: MatchSpec | None = None
    within_s: Seconds | None = None


class Eventually(ContractModel):
    timeout_s: Seconds
    interval_s: Seconds


class ExpectState(ContractModel):
    party: PartyRole
    query: SnakeId
    args: dict[str, Any] | None = None
    match: MatchSpec
    eventually: Eventually | None = None


class DoStep(ContractModel):
    kind: ClassVar[StepKind] = "do"
    do: DoAction


class SendStep(ContractModel):
    kind: ClassVar[StepKind] = "send"
    send: SendRequest


class EmitStep(ContractModel):
    kind: ClassVar[StepKind] = "emit"
    emit: EmitWebhook


class ExpectReceivedStep(ContractModel):
    kind: ClassVar[StepKind] = "expect_received"
    expect_received: ExpectReceived


class ExpectNoRequestStep(ContractModel):
    kind: ClassVar[StepKind] = "expect_no_request"
    expect_no_request: ExpectNoRequest


class ExpectEmittedStep(ContractModel):
    kind: ClassVar[StepKind] = "expect_emitted"
    expect_emitted: ExpectEmitted


class ExpectStateStep(ContractModel):
    kind: ClassVar[StepKind] = "expect_state"
    expect_state: ExpectState


def step_kind(value: Any) -> str | None:
    if isinstance(value, ContractModel):
        kind = getattr(value, "kind", None)
        return kind if isinstance(kind, str) else None
    if isinstance(value, dict) and len(value) == 1:
        key = next(iter(value))
        return key if isinstance(key, str) else None
    return None


Step = Annotated[
    Annotated[DoStep, Tag("do")]
    | Annotated[SendStep, Tag("send")]
    | Annotated[EmitStep, Tag("emit")]
    | Annotated[ExpectReceivedStep, Tag("expect_received")]
    | Annotated[ExpectNoRequestStep, Tag("expect_no_request")]
    | Annotated[ExpectEmittedStep, Tag("expect_emitted")]
    | Annotated[ExpectStateStep, Tag("expect_state")],
    Discriminator(
        step_kind,
        custom_error_type="invalid_step",
        custom_error_message=f"A step must have exactly one key: {', '.join(STEP_KINDS)}",
    ),
]


class Scenario(ContractModel):
    id: UpperId
    title: NonEmptyText
    side: Side
    covers: list[NonEmptyText] = Field(default_factory=list)
    steps: Annotated[list[Step], Field(min_length=1)]


class Contract(ContractModel):
    doubleblind: Literal["1.0"]
    id: Slug
    version: Annotated[int, Field(ge=1)]
    status: ContractStatus
    parties: Parties
    environments: dict[str, str]
    secrets: list[Secret] = Field(default_factory=list)
    glossary: dict[str, str] = Field(default_factory=dict)
    schemas: dict[SchemaName, DataSchema] = Field(default_factory=dict)
    operations: Annotated[dict[SnakeId, Operation], Field(min_length=1)]
    webhooks: dict[SnakeId, Webhook] = Field(default_factory=dict)
    semantic_mappings: dict[SnakeId, dict[str, str]] = Field(default_factory=dict)
    decisions: list[Decision] = Field(default_factory=list)
    scenarios: Annotated[list[Scenario], Field(min_length=1)]

    def scenarios_for(self, side: Side) -> list[Scenario]:
        return [scenario for scenario in self.scenarios if scenario.side == side]


class HttpCall(ContractModel):
    method: HttpMethod
    path: RequestPath
    headers: dict[str, str] | None = None
    query: dict[str, str] | None = None
    body: Any = None
    capture: dict[SnakeId, JsonPath] | None = None


class Healthcheck(ContractModel):
    method: HttpMethod = "GET"
    path: RequestPath


class AppSpec(ContractModel):
    manage: bool
    start: NonEmptyText | None = None
    cwd: str = "."
    env: dict[str, str] = Field(default_factory=dict)
    healthcheck: Healthcheck | None = None

    @model_validator(mode="after")
    def managed_app_needs_start_command(self) -> Self:
        if self.manage and self.start is None:
            raise ValueError("an app with manage: true needs a start command")
        return self


class Binding(ContractModel):
    doubleblind_binding: Literal["1.0"]
    side: PartyRole
    base_url: NonEmptyText
    webhook_receiver_url: NonEmptyText | None = None
    app: AppSpec | None = None
    actions: dict[SnakeId, HttpCall]
    queries: dict[SnakeId, HttpCall] = Field(default_factory=dict)

    @model_validator(mode="after")
    def consumer_needs_webhook_receiver(self) -> Self:
        if self.side == "consumer" and self.webhook_receiver_url is None:
            raise ValueError("a consumer binding needs webhook_receiver_url")
        return self
