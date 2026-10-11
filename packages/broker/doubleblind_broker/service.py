import asyncio
import hashlib
import json
import logging
import secrets as secret_tokens
from collections import defaultdict
from collections.abc import Coroutine
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any, Literal, NoReturn, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine
from sqlmodel import Session, col, select

from doubleblind_broker import contracts
from doubleblind_broker.autopilot import DefaultOptionSimulator, HumanSimulator, autopilot_answer
from doubleblind_broker.db import (
    ContractVersionRecord,
    PartyRecord,
    QuestionRecord,
    SessionRecord,
    SignatureRecord,
    TestRunRecord,
    TurnRecord,
    open_session,
)
from doubleblind_broker.e2e import E2EExecutor, E2ERequest, UnavailableE2E
from doubleblind_broker.events import EventLog, utc_now
from doubleblind_broker.gateway import (
    Allowlist,
    GatewayError,
    IntegrationGateway,
    build_allowlist,
)
from doubleblind_broker.presets import Preset, PresetNotFoundError, load_preset
from doubleblind_broker.state_machine import (
    NEGOTIATION_STATES,
    TERMINAL_STATES,
    Effect,
    State,
    Trigger,
    transition,
)
from doubleblind_broker.tokens import Principal, hash_token, new_token
from doubleblind_contract.guard_rules import (
    GuardVerdict,
    check_message,
    check_test_run,
    check_turn,
)
from doubleblind_contract.loader import load_binding_text, load_contract_text, parse_yaml
from doubleblind_contract.runtime import RunReport
from doubleblind_contract.validate import (
    check_binding_text,
    signing_problems,
    validate_contract_data,
)
from doubleblind_sandbox.provider import ProbeResult

logger = logging.getLogger(__name__)
MAX_E2E_RUNS = 3
AMENDMENT_ROUNDS = 8
GATEWAY_FEEDBACK_FIELDS = ("party", "source", "method", "status", "violations")

Role = Literal["consumer", "provider"]
TurnStatus = Literal["continue", "ready_to_sign", "blocked"]


class BrokerError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict[str, Any]:
        body: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.details is not None:
            body["details"] = self.details
        return body


PROBE_PATH = "/__isolation-probe/not-in-contract"


class IsolationProbe(Protocol):
    async def run(self) -> list[ProbeResult]: ...


class QuestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str | None = None
    text: str = Field(min_length=1)
    options: list[str] = Field(default_factory=list)
    default: str | None = None


class TurnIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    round: int = Field(ge=1)
    based_on_version: int = Field(ge=1)
    message: str
    patches: list[dict[str, Any]] = Field(default_factory=list)
    questions_for_human: list[QuestionIn] = Field(default_factory=list)
    status: TurnStatus = "continue"


class ScenarioResultIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_id: str
    status: Literal["passed", "failed", "error"]
    sanitized_reason: str | None = None


class TestRunIn(BaseModel):
    __test__ = False
    model_config = ConfigDict(extra="forbid")

    side: Role
    contract_version: int
    summary: dict[str, int] = Field(default_factory=dict)
    results: list[ScenarioResultIn]
    binding_yaml: str | None = None
    workspace_image: str | None = Field(default=None, pattern=r"^[A-Za-z0-9:._/@-]{1,200}$")


class ContractGapIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_ids: list[str] = Field(default_factory=list)
    description: str = Field(min_length=1)


class PsmBuiltIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["psm.built"]
    files: int = Field(ge=0)
    tokens: int = Field(ge=0)
    seconds: float = Field(ge=0)


class EgressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["egress.blocked", "egress.redacted"]
    kind: str = Field(max_length=40)
    reason: str = Field(max_length=300)


class LlmCallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["llm.call"]
    role: str = Field(max_length=40)
    model: str = Field(max_length=120)
    tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0)
    latency_ms: int = Field(ge=0)


class CoderProgressIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["coder.progress"]
    step: int = Field(ge=0)
    files_changed: int = Field(ge=0)
    note: str = Field(max_length=120)


class SandboxCostIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["sandbox.cost"]
    cost_usd: float = Field(ge=0)


AgentEventIn = Annotated[
    PsmBuiltIn | EgressIn | LlmCallIn | CoderProgressIn | SandboxCostIn,
    Field(discriminator="type"),
]


@dataclass
class BrokerSettings:
    presets_dir: Path
    max_rounds: int = 14
    autopilot: bool = False
    autopilot_delay_s: float = 1.5
    admin_token: str | None = None
    next_action_wait_s: float = 25.0


@dataclass
class Outbox:
    events: list[tuple[str, str, dict[str, Any], str]] = field(default_factory=list)

    def add(
        self, type_: str, actor: str, payload: dict[str, Any], visibility: str = "shared"
    ) -> None:
        self.events.append((type_, actor, payload, visibility))


def other(roles: dict[Role, PartyRecord], party_key: str) -> PartyRecord:
    return next(party for party in roles.values() if party.party_key != party_key)


class BrokerService:
    def __init__(
        self,
        engine: Engine,
        settings: BrokerSettings,
        e2e_executor: E2EExecutor | None = None,
        simulator: HumanSimulator | None = None,
        isolation_probe: IsolationProbe | None = None,
    ) -> None:
        self.engine = engine
        self.settings = settings
        self.isolation_probe = isolation_probe
        self.e2e_executor: E2EExecutor = e2e_executor or UnavailableE2E()
        self.simulator: HumanSimulator = simulator or DefaultOptionSimulator()
        self.events = EventLog(engine)
        self.autopilot_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.background: set[asyncio.Task[None]] = set()
        self.presets: dict[str, Preset] = {}
        self.gateway = IntegrationGateway(self.gateway_allowlist, self.gateway_event)

    def preset(self, name: str) -> Preset:
        if name not in self.presets:
            try:
                self.presets[name] = load_preset(self.settings.presets_dir, name)
            except PresetNotFoundError as error:
                raise BrokerError(400, "UNKNOWN_PRESET", str(error)) from error
        return self.presets[name]

    def flush(self, session_id: str, outbox: Outbox) -> None:
        for type_, actor, payload, visibility in outbox.events:
            self.events.append(session_id, type_, actor, payload, visibility)

    def spawn(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.get_running_loop().create_task(coroutine)
        self.background.add(task)
        task.add_done_callback(self.background.discard)

    async def wait_for_background(self) -> None:
        while pending := [task for task in self.background if not task.done()]:
            await asyncio.wait(pending)

    def resolve(self, token: str | None) -> Principal:
        if not token:
            raise BrokerError(401, "UNAUTHORIZED", "missing bearer token")
        if self.settings.admin_token and token == self.settings.admin_token:
            return Principal("admin")
        hashed = hash_token(token)
        with open_session(self.engine) as db:
            observer = db.exec(
                select(SessionRecord).where(SessionRecord.observer_token_hash == hashed)
            ).first()
            if observer is not None:
                return Principal("observer", observer.id)
            agent = db.exec(
                select(PartyRecord).where(PartyRecord.agent_token_hash == hashed)
            ).first()
            if agent is not None:
                return Principal("agent", agent.session_id, agent.party_key)
            human = db.exec(
                select(PartyRecord).where(PartyRecord.human_token_hash == hashed)
            ).first()
            if human is not None:
                return Principal("human", human.session_id, human.party_key)
        raise BrokerError(401, "UNAUTHORIZED", "unknown token")

    def authorize(self, principal: Principal, session_id: str, *kinds: str) -> None:
        if principal.kind == "admin":
            return
        if principal.session_id != session_id:
            raise BrokerError(403, "FORBIDDEN", "this token belongs to another session")
        if kinds and principal.kind not in kinds:
            raise BrokerError(403, "FORBIDDEN", f"this action needs a {' or '.join(kinds)} token")

    def session_record(self, db: Session, session_id: str) -> SessionRecord:
        record = db.get(SessionRecord, session_id)
        if record is None:
            raise BrokerError(404, "NO_SUCH_SESSION", f"session {session_id} does not exist")
        return record

    def roles(self, db: Session, session_id: str) -> dict[Role, PartyRecord]:
        records = db.exec(select(PartyRecord).where(PartyRecord.session_id == session_id)).all()
        return {
            ("consumer" if record.role == "consumer" else "provider"): record for record in records
        }

    def party(self, db: Session, session_id: str, party_key: str | None) -> PartyRecord:
        for record in self.roles(db, session_id).values():
            if record.party_key == party_key:
                return record
        raise BrokerError(403, "FORBIDDEN", "this action needs a party token")

    def contract_text(self, db: Session, session_id: str, version: int) -> str:
        record = db.get(ContractVersionRecord, (session_id, version))
        if record is None:
            raise BrokerError(404, "NO_SUCH_VERSION", f"contract version {version} does not exist")
        return record.yaml_text

    def current_contract(self, db: Session, record: SessionRecord) -> dict[str, Any]:
        return parse_yaml(self.contract_text(db, record.id, record.current_contract_version))

    def store_version(
        self,
        db: Session,
        record: SessionRecord,
        contract: dict[str, Any],
        created_by: str,
        outbox: Outbox,
    ) -> int:
        old_text = self.contract_text(db, record.id, record.current_contract_version)
        version = record.current_contract_version + 1
        text = contracts.dump_yaml(contracts.with_version(contract, version, "proposed"))
        db.add(
            ContractVersionRecord(
                session_id=record.id,
                version=version,
                yaml_text=text,
                created_by=created_by,
                created_at=utc_now(),
            )
        )
        record.current_contract_version = version
        outbox.add(
            "contract.version_created",
            created_by,
            {"version": version, "diff_stats": contracts.diff_stats(old_text, text)},
        )
        return version

    def update_metrics(self, record: SessionRecord, outbox: Outbox, **changes: float) -> None:
        metrics = json.loads(record.metrics_json)
        for key, value in changes.items():
            metrics[key] = metrics.get(key, 0) + value
        record.metrics_json = json.dumps(metrics)
        outbox.add("metrics.update", "broker", metrics, "observer")

    def guard(
        self,
        record: SessionRecord,
        party_key: str,
        verdict: GuardVerdict,
        outbox: Outbox,
        db: Session,
        kind: str,
        payload: Any,
    ) -> None:
        if not verdict.blocked:
            size = len(json.dumps(payload, ensure_ascii=False).encode())
            self.update_metrics(
                record, outbox, code_lines_shared=verdict.code_lines, **{f"bytes_{kind}": size}
            )
            return
        self.update_metrics(record, outbox, blocked_count=1)
        outbox.add(
            "egress.blocked", "broker", {"party": party_key, "reason": verdict.reasons}, party_key
        )
        db.add(record)
        db.commit()
        self.flush(record.id, outbox)
        raise BrokerError(
            422, "EGRESS_BLOCKED", "the message breaks the egress rules", verdict.reasons
        )

    def apply_trigger(
        self, db: Session, record: SessionRecord, trigger: Trigger, outbox: Outbox
    ) -> tuple[Effect, ...]:
        old = State(record.state)
        new, effects = transition(old, trigger)
        record.state = new.value
        record.updated_at = utc_now()
        outbox.add("session.state_changed", "broker", {"from": old.value, "to": new.value})
        roles = self.roles(db, record.id)
        for effect in effects:
            self.apply_effect(db, record, roles, effect, outbox)
        for party in roles.values():
            db.add(party)
        return effects

    def apply_effect(
        self,
        db: Session,
        record: SessionRecord,
        roles: dict[Role, PartyRecord],
        effect: Effect,
        outbox: Outbox,
    ) -> None:
        if effect is Effect.REQUEST_BRIEFS:
            for party in roles.values():
                party.status = "briefing"
        elif effect is Effect.PROVIDER_TURN:
            record.turn_party = roles["provider"].party_key
            for party in roles.values():
                party.status = "negotiating"
        elif effect is Effect.REQUEST_SIGNATURES:
            for party in roles.values():
                party.status = "awaiting_signature"
            if self.settings.autopilot:
                self.spawn(self.autopilot_sign(record.id))
        elif effect is Effect.NOTIFY_HUMANS:
            outbox.add("session.escalated", "broker", {"round": record.round})
        elif effect is Effect.MARK_SIGNED:
            self.mark_signed(db, record, roles, outbox)
        elif effect is Effect.GENERATE_SECRETS:
            contract = self.current_contract(db, record)
            names = [secret["name"] for secret in contract.get("secrets", [])]
            record.secrets_json = json.dumps({name: new_token() for name in names})
        elif effect is Effect.REQUEST_IMPLEMENTATION:
            record.e2e_feedback_json = None
            for party in roles.values():
                party.status = "implementing"
        elif effect is Effect.RUN_E2E:
            self.spawn(self.run_e2e(record.id))
        elif effect is Effect.ISSUE_CERTIFICATE:
            outbox.add("certificate.issued", "broker", {"version": record.signed_contract_version})
        elif effect is Effect.SHARE_E2E_REPORT:
            for party in roles.values():
                party.status = "implementing"

    def mark_signed(
        self, db: Session, record: SessionRecord, roles: dict[Role, PartyRecord], outbox: Outbox
    ) -> None:
        version = record.current_contract_version
        stored = db.get_one(ContractVersionRecord, (record.id, version))
        signed = contracts.mark_signed(
            parse_yaml(stored.yaml_text),
            [roles["consumer"].party_key, roles["provider"].party_key],
        )
        stored.yaml_text = contracts.dump_yaml(signed)
        db.add(stored)
        record.signed_contract_version = version
        outbox.add("contract.signed", "broker", {"version": version})

    def create_session(self, principal: Principal, name: str, preset_name: str) -> dict[str, Any]:
        if self.settings.admin_token and principal.kind != "admin":
            raise BrokerError(403, "FORBIDDEN", "creating sessions needs the admin token")
        preset = self.preset(preset_name)
        session_id = f"ses_{secret_tokens.token_hex(6)}"
        tokens = {"observer": new_token()}
        now = utc_now()
        record = SessionRecord(
            id=session_id,
            name=name,
            preset=preset_name,
            state=State.CREATED.value,
            max_rounds=self.settings.max_rounds,
            created_at=now,
            updated_at=now,
            consumer_party_id=preset.consumer.party,
            provider_party_id=preset.provider.party,
            observer_token_hash=hash_token(tokens["observer"]),
        )
        parties = []
        for role, spec in (("consumer", preset.consumer), ("provider", preset.provider)):
            tokens[f"agent_{spec.party}"] = new_token()
            tokens[f"human_{spec.party}"] = new_token()
            parties.append(
                PartyRecord(
                    session_id=session_id,
                    party_key=spec.party,
                    role=role,
                    display_name=spec.display_name,
                    agent_token_hash=hash_token(tokens[f"agent_{spec.party}"]),
                    human_token_hash=hash_token(tokens[f"human_{spec.party}"]),
                )
            )
        outbox = Outbox()
        with open_session(self.engine) as db:
            db.add(record)
            for party in parties:
                db.add(party)
            db.add(
                ContractVersionRecord(
                    session_id=session_id,
                    version=1,
                    yaml_text=contracts.dump_yaml(contracts.skeleton(preset)),
                    created_by="broker",
                    created_at=now,
                )
            )
            db.commit()
        outbox.add(
            "session.created",
            "broker",
            {
                "name": name,
                "preset": preset_name,
                "consumer": preset.consumer.party,
                "provider": preset.provider.party,
            },
        )
        self.flush(session_id, outbox)
        return {"session_id": session_id, "tokens": tokens}

    def start(self, principal: Principal, session_id: str) -> dict[str, Any]:
        self.authorize(principal, session_id, "observer", "admin")
        outbox = Outbox()
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            self.require_state(record, State.CREATED)
            self.apply_trigger(db, record, Trigger.START, outbox)
            db.add(record)
            db.commit()
        self.flush(session_id, outbox)
        return self.session_view(principal, session_id)

    def require_state(self, record: SessionRecord, *states: State) -> None:
        if State(record.state) not in states:
            expected = ", ".join(state.value for state in states)
            raise BrokerError(
                409, "WRONG_STATE", f"session is {record.state}, this needs {expected}"
            )

    def session_view(self, principal: Principal, session_id: str) -> dict[str, Any]:
        self.authorize(principal, session_id)
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            roles = self.roles(db, session_id)
            unanswered = self.unanswered_questions(db, session_id)
        return {
            "id": record.id,
            "name": record.name,
            "preset": record.preset,
            "state": record.state,
            "round": record.round,
            "max_rounds": record.max_rounds,
            "turn_party": record.turn_party,
            "current_contract_version": record.current_contract_version,
            "signed_contract_version": record.signed_contract_version,
            "open_questions": unanswered,
            "parties": [
                {
                    "party": party.party_key,
                    "role": role,
                    "display_name": party.display_name,
                    "status": party.status,
                    "brief_submitted": party.brief_json is not None,
                    "binding_submitted": party.binding_yaml is not None,
                }
                for role, party in roles.items()
            ],
            "metrics": json.loads(record.metrics_json),
        }

    def unanswered_questions(self, db: Session, session_id: str) -> int:
        return len(
            db.exec(
                select(QuestionRecord).where(
                    QuestionRecord.session_id == session_id, col(QuestionRecord.answer).is_(None)
                )
            ).all()
        )

    async def next_action(
        self, principal: Principal, session_id: str, wait_s: float | None = None
    ) -> dict[str, Any]:
        self.authorize(principal, session_id, "agent")
        remaining = self.settings.next_action_wait_s if wait_s is None else wait_s
        loop = asyncio.get_running_loop()
        deadline = loop.time() + remaining
        changes = self.events.subscribe(session_id)
        try:
            while True:
                action = self.compute_action(session_id, principal.party_key)
                remaining = deadline - loop.time()
                if action["action"] != "wait" or remaining <= 0:
                    return action
                try:
                    await asyncio.wait_for(changes.get(), remaining)
                except TimeoutError:
                    return action
        finally:
            self.events.unsubscribe(session_id, changes)

    def compute_action(self, session_id: str, party_key: str | None) -> dict[str, Any]:
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            party = self.party(db, session_id, party_key)
            state = State(record.state)
            if state in TERMINAL_STATES:
                return {"action": "done", "context": {"state": state.value}}
            if state is State.BRIEFING and party.brief_json is None:
                allowlist = self.preset(record.preset).synthetic_allowlist.model_dump()
                return {
                    "action": "build_psm_and_brief",
                    "context": {"role": party.role, "synthetic_allowlist": allowlist},
                }
            if (
                state in NEGOTIATION_STATES
                and record.turn_party == party.party_key
                and not self.unanswered_questions(db, session_id)
            ):
                turn_context: dict[str, Any] = {
                    "role": party.role,
                    "round": record.round + 1,
                    "based_on_version": record.current_contract_version,
                }
                if state is State.AMENDING:
                    gaps = [
                        event["payload"]
                        for event in self.events.list(session_id)
                        if event["type"] == "contract.gap"
                    ]
                    return {"action": "amend", "context": {**turn_context, "gap": gaps[-1]}}
                return {"action": "negotiate", "context": turn_context}
            if state is State.IMPLEMENTING and party.status == "implementing":
                context: dict[str, Any] = {
                    "role": party.role,
                    "signed_version": record.signed_contract_version,
                    "secrets": json.loads(record.secrets_json),
                }
                if record.e2e_feedback_json is not None:
                    context["sanitized_e2e"] = json.loads(record.e2e_feedback_json)
                    return {"action": "fix_after_e2e", "context": context}
                return {"action": "implement", "context": context}
            return {"action": "wait", "context": {"state": state.value}}

    def submit_brief(
        self, principal: Principal, session_id: str, brief: dict[str, Any]
    ) -> dict[str, Any]:
        self.authorize(principal, session_id, "agent")
        outbox = Outbox()
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            self.require_state(record, State.BRIEFING)
            party = self.party(db, session_id, principal.party_key)
            if party.brief_json is not None:
                raise BrokerError(409, "BRIEF_EXISTS", "the brief was already submitted")
            preset = self.preset(record.preset)
            self.guard(
                record,
                party.party_key,
                check_message(brief, preset.allowlist()),
                outbox,
                db,
                "brief",
                brief,
            )
            party.brief_json = json.dumps(brief, ensure_ascii=False)
            party.status = "briefed"
            db.add(party)
            outbox.add(
                "brief.submitted", party.party_key, {"party": party.party_key, "brief": brief}
            )
            if all(p.brief_json is not None for p in self.roles(db, session_id).values()):
                self.apply_trigger(db, record, Trigger.BRIEFS_COMPLETE, outbox)
            db.add(record)
            db.commit()
        self.flush(session_id, outbox)
        return {"accepted": True}

    def counterparty_brief(self, principal: Principal, session_id: str) -> dict[str, Any]:
        self.authorize(principal, session_id, "agent", "human")
        with open_session(self.engine) as db:
            mine = self.party(db, session_id, principal.party_key)
            party = other(self.roles(db, session_id), mine.party_key)
        if party.brief_json is None:
            raise BrokerError(404, "NO_BRIEF", f"{party.party_key} has not submitted a brief yet")
        brief: dict[str, Any] = json.loads(party.brief_json)
        return {"party": party.party_key, "role": party.role, "brief": brief}

    def submit_turn(self, principal: Principal, session_id: str, turn: TurnIn) -> dict[str, Any]:
        self.authorize(principal, session_id, "agent")
        outbox = Outbox()
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            self.require_state(record, *NEGOTIATION_STATES)
            party = self.party(db, session_id, principal.party_key)
            if record.turn_party != party.party_key:
                raise BrokerError(409, "NOT_YOUR_TURN", f"it is {record.turn_party}'s turn")
            if turn.round != record.round + 1:
                raise BrokerError(
                    409,
                    "WRONG_ROUND",
                    f"the next round is {record.round + 1}",
                    record.round + 1,
                )
            if turn.based_on_version != record.current_contract_version:
                raise BrokerError(
                    409,
                    "STALE_VERSION",
                    f"the current contract version is {record.current_contract_version}",
                    {"current_version": record.current_contract_version},
                )
            preset = self.preset(record.preset)
            payload = turn.model_dump()
            verdict = check_turn(payload, preset.allowlist())
            if verdict.blocked:
                self.reject_turn(db, record, party, turn, verdict.reasons, outbox)
                self.guard(record, party.party_key, verdict, outbox, db, "turns", payload)
            candidate = (
                self.patched_contract(db, record, party, turn, outbox)
                if turn.patches
                else self.current_contract(db, record)
            )
            if turn.status == "ready_to_sign" and (problems := signing_problems(candidate)):
                self.fail_turn(db, record, party, turn, problems, outbox)
            version_after = record.current_contract_version
            if turn.patches:
                version_after = self.store_version(db, record, candidate, party.party_key, outbox)
            self.guard(record, party.party_key, verdict, outbox, db, "turns", payload)
            question_ids = self.create_questions(
                db, record, party, turn.questions_for_human, outbox
            )
            db.add(
                TurnRecord(
                    session_id=session_id,
                    round=turn.round,
                    party_key=party.party_key,
                    based_on_version=turn.based_on_version,
                    message=turn.message,
                    patches_json=json.dumps(turn.patches, ensure_ascii=False),
                    status=turn.status,
                    accepted=True,
                    contract_version_after=version_after,
                )
            )
            record.round = turn.round
            party.last_turn_status = turn.status
            party.last_accepted_version = version_after
            party.last_turn_had_patches = bool(turn.patches)
            db.add(party)
            record.turn_party = other(self.roles(db, session_id), party.party_key).party_key
            outbox.add(
                "turn.accepted",
                party.party_key,
                {
                    "party": party.party_key,
                    "round": turn.round,
                    "message": turn.message,
                    "status": turn.status,
                    "patch_summary": contracts.patch_summary(turn.patches),
                    "contract_version": version_after,
                    "questions": question_ids,
                },
            )
            self.after_turn(db, record, outbox)
            if self.settings.autopilot:
                for qid in question_ids:
                    self.spawn(self.autopilot_answer(session_id, qid))
            db.add(record)
            db.commit()
        self.flush(session_id, outbox)
        return {
            "accepted": True,
            "round": turn.round,
            "contract_version": version_after,
            "questions": question_ids,
        }

    def reject_turn(
        self,
        db: Session,
        record: SessionRecord,
        party: PartyRecord,
        turn: TurnIn,
        errors: list[str],
        outbox: Outbox,
    ) -> None:
        db.add(
            TurnRecord(
                session_id=record.id,
                round=turn.round,
                party_key=party.party_key,
                based_on_version=turn.based_on_version,
                message=turn.message,
                patches_json=json.dumps(turn.patches, ensure_ascii=False),
                status=turn.status,
                accepted=False,
                errors_json=json.dumps(errors, ensure_ascii=False),
            )
        )
        outbox.add(
            "turn.rejected",
            "broker",
            {"party": party.party_key, "round": turn.round, "reason": errors},
            party.party_key,
        )

    def patched_contract(
        self,
        db: Session,
        record: SessionRecord,
        party: PartyRecord,
        turn: TurnIn,
        outbox: Outbox,
    ) -> dict[str, Any]:
        try:
            candidate = contracts.apply_patches(self.current_contract(db, record), turn.patches)
        except contracts.PatchError as error:
            self.fail_turn(db, record, party, turn, [str(error)], outbox)
        report = validate_contract_data(
            contracts.with_version(candidate, record.current_contract_version + 1, "proposed")
        )
        if not report.ok:
            self.fail_turn(db, record, party, turn, [f.render() for f in report.errors], outbox)
        return candidate

    def fail_turn(
        self,
        db: Session,
        record: SessionRecord,
        party: PartyRecord,
        turn: TurnIn,
        errors: list[str],
        outbox: Outbox,
    ) -> NoReturn:
        self.reject_turn(db, record, party, turn, errors, outbox)
        db.add(record)
        db.commit()
        self.flush(record.id, outbox)
        raise BrokerError(
            422, "INVALID_CONTRACT", "the patches do not give a valid contract", errors
        )

    def create_questions(
        self,
        db: Session,
        record: SessionRecord,
        party: PartyRecord,
        questions: list[QuestionIn],
        outbox: Outbox,
    ) -> list[str]:
        existing = len(
            db.exec(select(QuestionRecord).where(QuestionRecord.session_id == record.id)).all()
        )
        created: list[str] = []
        for offset, question in enumerate(questions, start=1):
            qid = f"Q{existing + offset}"
            db.add(
                QuestionRecord(
                    session_id=record.id,
                    id=qid,
                    party_key=party.party_key,
                    text=question.text,
                    options_json=json.dumps(question.options, ensure_ascii=False),
                    default=question.default,
                    created_at=utc_now(),
                )
            )
            created.append(qid)
            outbox.add(
                "question.created",
                party.party_key,
                {
                    "qid": qid,
                    "party": party.party_key,
                    "text": question.text,
                    "options": question.options,
                    "default": question.default,
                },
                party.party_key,
            )
        return created

    def after_turn(self, db: Session, record: SessionRecord, outbox: Outbox) -> None:
        db.flush()
        if self.converged(db, record):
            self.apply_trigger(db, record, Trigger.CONVERGED, outbox)
        elif record.round >= record.max_rounds:
            self.apply_trigger(db, record, Trigger.ROUNDS_EXHAUSTED, outbox)

    def converged(self, db: Session, record: SessionRecord) -> bool:
        parties = list(self.roles(db, record.id).values())
        if any(party.last_turn_status != "ready_to_sign" for party in parties):
            return False
        if any(party.last_accepted_version != record.current_contract_version for party in parties):
            return False
        latest = next(party for party in parties if party.party_key != record.turn_party)
        if latest.last_turn_had_patches:
            return False
        if self.unanswered_questions(db, record.id):
            return False
        return validate_contract_data(self.current_contract(db, record)).ok

    def list_turns(self, principal: Principal, session_id: str) -> list[dict[str, Any]]:
        self.authorize(principal, session_id)
        with open_session(self.engine) as db:
            turns = db.exec(
                select(TurnRecord)
                .where(TurnRecord.session_id == session_id)
                .order_by(col(TurnRecord.id))
            ).all()
        return [
            {
                "round": turn.round,
                "party": turn.party_key,
                "based_on_version": turn.based_on_version,
                "message": turn.message,
                "patches": json.loads(turn.patches_json),
                "status": turn.status,
                "accepted": turn.accepted,
                "errors": json.loads(turn.errors_json),
                "contract_version_after": turn.contract_version_after,
            }
            for turn in turns
            if turn.accepted or principal.can_see(turn.party_key)
        ]

    def get_contract(self, principal: Principal, session_id: str, version: int | None) -> str:
        self.authorize(principal, session_id)
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            return self.contract_text(db, session_id, version or record.current_contract_version)

    def contract_diff(self, principal: Principal, session_id: str, old: int, new: int) -> str:
        self.authorize(principal, session_id)
        with open_session(self.engine) as db:
            return contracts.unified_diff(
                self.contract_text(db, session_id, old),
                self.contract_text(db, session_id, new),
                f"v{old}",
                f"v{new}",
            )

    def list_questions(self, principal: Principal, session_id: str) -> list[dict[str, Any]]:
        self.authorize(principal, session_id)
        with open_session(self.engine) as db:
            questions = db.exec(
                select(QuestionRecord).where(QuestionRecord.session_id == session_id)
            ).all()
        return [
            {
                "id": question.id,
                "party": question.party_key,
                "text": question.text,
                "options": json.loads(question.options_json),
                "default": question.default,
                "answer": question.answer,
                "answered_by": question.answered_by,
                "decision_id": question.decision_id,
            }
            for question in sorted(questions, key=lambda item: int(item.id[1:]))
            if principal.can_see(question.party_key)
        ]

    def answer_question(self, principal: Principal, qid: str, answer: str) -> dict[str, Any]:
        if principal.kind != "human" or principal.session_id is None:
            raise BrokerError(403, "FORBIDDEN", "this action needs a human token")
        return self.record_answer(
            principal.session_id, qid, answer, principal.actor, principal.party_key
        )

    def record_answer(
        self, session_id: str, qid: str, answer: str, actor: str, party_key: str | None
    ) -> dict[str, Any]:
        outbox = Outbox()
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            self.require_state(record, *NEGOTIATION_STATES, State.ESCALATED)
            question = db.get(QuestionRecord, (session_id, qid))
            if question is None:
                raise BrokerError(404, "NO_SUCH_QUESTION", f"question {qid} does not exist")
            if question.party_key != party_key:
                raise BrokerError(403, "FORBIDDEN", "this question belongs to the other party")
            if question.answer is not None:
                raise BrokerError(409, "ALREADY_ANSWERED", f"{qid} is already answered")
            asking = self.party(db, session_id, question.party_key)
            contract = self.current_contract(db, record)
            decision_id = contracts.human_decision_id(contract, qid)
            decision = {
                "id": decision_id,
                "text": f"{question.text} Odpowiedź: {answer}",
                "owner": asking.role,
                "origin_question": qid,
            }
            self.store_version(
                db, record, contracts.add_decision(contract, decision), actor, outbox
            )
            question.answer = answer
            question.answered_by = actor
            question.decision_id = decision_id
            db.add(question)
            outbox.add(
                "question.answered",
                actor,
                {"qid": qid, "answer": answer, "decision_id": decision_id},
                question.party_key,
            )
            if State(record.state) is State.ESCALATED:
                self.apply_trigger(db, record, Trigger.HUMAN_ACTION, outbox)
            db.add(record)
            db.commit()
        self.flush(session_id, outbox)
        return {"qid": qid, "decision_id": decision_id}

    def resume(self, principal: Principal, session_id: str, extra_rounds: int) -> dict[str, Any]:
        self.authorize(principal, session_id, "human", "observer", "admin")
        outbox = Outbox()
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            self.require_state(record, State.ESCALATED)
            record.max_rounds = max(record.max_rounds, record.round) + extra_rounds
            self.apply_trigger(db, record, Trigger.HUMAN_ACTION, outbox)
            db.add(record)
            db.commit()
        self.flush(session_id, outbox)
        return self.session_view(principal, session_id)

    def sign(self, principal: Principal, session_id: str, version: int) -> dict[str, Any]:
        self.authorize(principal, session_id, "human")
        return self.record_signature(session_id, principal.party_key or "", version)

    def record_signature(self, session_id: str, party_key: str, version: int) -> dict[str, Any]:
        outbox = Outbox()
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            self.require_state(record, State.AWAITING_SIGNATURES)
            if version != record.current_contract_version:
                raise BrokerError(
                    409,
                    "STALE_VERSION",
                    f"sign the current version {record.current_contract_version}",
                    {"current_version": record.current_contract_version},
                )
            party = self.party(db, session_id, party_key)
            if db.get(SignatureRecord, (session_id, party.party_key, version)) is None:
                db.add(
                    SignatureRecord(
                        session_id=session_id,
                        party_key=party.party_key,
                        version=version,
                        signed_at=utc_now(),
                    )
                )
                party.status = "signed"
                db.add(party)
                outbox.add(
                    "signature.added",
                    f"human:{party.party_key}",
                    {"party": party.party_key, "version": version},
                )
            db.flush()
            signatures = db.exec(
                select(SignatureRecord).where(
                    SignatureRecord.session_id == session_id, SignatureRecord.version == version
                )
            ).all()
            if len({signature.party_key for signature in signatures}) == 2:
                self.apply_trigger(db, record, Trigger.BOTH_SIGNED, outbox)
            db.add(record)
            db.commit()
        self.flush(session_id, outbox)
        return {"signed": True, "version": version}

    def submit_test_run(
        self, principal: Principal, session_id: str, run: TestRunIn
    ) -> dict[str, Any]:
        self.authorize(principal, session_id, "agent")
        outbox = Outbox()
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            self.require_state(record, State.IMPLEMENTING)
            party = self.party(db, session_id, principal.party_key)
            if run.side != party.role:
                raise BrokerError(422, "WRONG_SIDE", f"{party.party_key} is the {party.role}")
            if run.contract_version != record.signed_contract_version:
                raise BrokerError(
                    409,
                    "STALE_VERSION",
                    f"test the signed version {record.signed_contract_version}",
                )
            preset = self.preset(record.preset)
            self.guard(
                record,
                party.party_key,
                check_test_run(run.model_dump(exclude={"workspace_image"}), preset.allowlist()),
                outbox,
                db,
                "results",
                run.model_dump(exclude={"workspace_image"}),
            )
            if run.binding_yaml is not None:
                self.accept_binding(party, run.binding_yaml)
            if run.workspace_image is not None:
                party.workspace_image = run.workspace_image
            db.add(
                TestRunRecord(
                    session_id=session_id,
                    party_key=party.party_key,
                    side=run.side,
                    contract_version=run.contract_version,
                    summary_json=json.dumps(run.summary),
                    results_json=json.dumps([result.model_dump() for result in run.results]),
                    created_at=utc_now(),
                )
            )
            contract = load_contract_text(self.contract_text(db, session_id, run.contract_version))
            required = {scenario.id for scenario in contract.scenarios_for(run.side)}
            passed = {result.scenario_id for result in run.results if result.status == "passed"}
            ready = required <= passed and party.binding_yaml is not None
            party.status = "ready_for_e2e" if ready else "implementing"
            db.add(party)
            outbox.add(
                "tests.result",
                party.party_key,
                {
                    "party": party.party_key,
                    "side": run.side,
                    "summary": run.summary,
                    "results": [result.model_dump() for result in run.results],
                },
            )
            outbox.add(
                "party.status",
                "broker",
                {"party": party.party_key, "status": party.status},
            )
            db.flush()
            if all(p.status == "ready_for_e2e" for p in self.roles(db, session_id).values()):
                self.apply_trigger(db, record, Trigger.BOTH_READY, outbox)
            db.add(record)
            db.commit()
        self.flush(session_id, outbox)
        return {"ready_for_e2e": ready}

    def accept_binding(self, party: PartyRecord, binding_yaml: str) -> None:
        binding, report = check_binding_text(binding_yaml)
        if binding is None:
            raise BrokerError(
                422,
                "INVALID_BINDING",
                "the binding is invalid",
                [f.render() for f in report.findings],
            )
        if binding.side != party.role:
            raise BrokerError(422, "INVALID_BINDING", f"the binding must have side {party.role}")
        party.binding_yaml = binding_yaml

    def contract_gap(
        self, principal: Principal, session_id: str, gap: ContractGapIn
    ) -> dict[str, Any]:
        self.authorize(principal, session_id, "agent")
        outbox = Outbox()
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            self.require_state(record, State.IMPLEMENTING)
            party = self.party(db, session_id, principal.party_key)
            preset = self.preset(record.preset)
            self.guard(
                record,
                party.party_key,
                check_message(gap.model_dump(), preset.allowlist()),
                outbox,
                db,
                "results",
                gap.model_dump(),
            )
            outbox.add(
                "contract.gap",
                party.party_key,
                {"party": party.party_key, **gap.model_dump()},
            )
            self.apply_trigger(db, record, Trigger.CONTRACT_GAP, outbox)
            record.turn_party = party.party_key
            record.max_rounds = record.round + AMENDMENT_ROUNDS
            for member in self.roles(db, session_id).values():
                member.status = "amending"
                member.last_turn_status = None
                member.last_accepted_version = None
                db.add(member)
            db.add(record)
            db.commit()
        self.flush(session_id, outbox)
        return {"state": State.AMENDING.value}

    def e2e_request(self, session_id: str) -> E2ERequest:
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            roles = self.roles(db, session_id)
            version = record.current_contract_version
            contract_yaml = self.contract_text(db, session_id, version)
        consumer, provider = roles["consumer"], roles["provider"]
        if consumer.binding_yaml is None or provider.binding_yaml is None:
            raise BrokerError(409, "MISSING_BINDING", "both parties must submit their binding")
        return E2ERequest(
            session_id=session_id,
            contract_yaml=contract_yaml,
            contract_id=parse_yaml(contract_yaml)["id"],
            contract_version=version,
            consumer_party=consumer.party_key,
            provider_party=provider.party_key,
            consumer_binding=consumer.binding_yaml,
            provider_binding=provider.binding_yaml,
            secrets=json.loads(record.secrets_json),
            images={
                party.party_key: party.workspace_image
                for party in (consumer, provider)
                if party.workspace_image is not None
            },
            relay_tokens={
                party.party_key: self.gateway.issue_token(session_id, party.party_key)
                for party in (consumer, provider)
            },
        )

    def trigger_e2e(self, principal: Principal, session_id: str) -> dict[str, Any]:
        self.authorize(principal, session_id, "observer", "admin")
        with open_session(self.engine) as db:
            self.require_state(self.session_record(db, session_id), State.E2E)
        self.spawn(self.run_e2e(session_id))
        return {"started": True}

    async def run_e2e(self, session_id: str) -> None:
        started = Outbox()
        started.add("e2e.started", "broker", {})
        self.flush(session_id, started)
        try:
            request = self.e2e_request(session_id)
            report, cost_usd = await asyncio.to_thread(self.e2e_executor.run, request)
        except Exception as error:
            logger.exception("end-to-end run failed for %s", session_id)
            message = f"{type(error).__name__}: {error}"
            failure = {
                "scenarios": [
                    {
                        "id": "E2E",
                        "title": "end-to-end run",
                        "status": "error",
                        "failures": [{"message": message}],
                    }
                ]
            }
            self.finish_e2e(session_id, None, failure, 0.0)
            return
        finally:
            self.gateway.revoke(session_id)
        self.finish_e2e(session_id, report, None, cost_usd)

    def gateway_traffic(self, session_id: str) -> list[dict[str, Any]]:
        events = self.events.list(session_id, 0)
        start = max(event["seq"] for event in events if event["type"] == "e2e.started")
        return [
            {key: event["payload"].get(key) for key in GATEWAY_FEEDBACK_FIELDS}
            for event in events
            if event["type"] == "gateway.request" and event["seq"] > start
        ]

    def finish_e2e(
        self,
        session_id: str,
        report: RunReport | None,
        failure: dict[str, Any] | None,
        cost_usd: float,
    ) -> None:
        outbox = Outbox()
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            if State(record.state) is not State.E2E:
                return
            sanitized = report.to_dict(include_logs=False) if report is not None else failure or {}
            passed = report is not None and report.ok and bool(report.scenarios)
            db.add(
                TestRunRecord(
                    session_id=session_id,
                    party_key="broker",
                    side="e2e",
                    contract_version=record.current_contract_version,
                    summary_json=json.dumps(report.summary if report else {"error": 1}),
                    results_json=json.dumps(sanitized, ensure_ascii=False, default=str),
                    created_at=utc_now(),
                )
            )
            db.flush()
            if cost_usd:
                self.update_metrics(record, outbox, sandbox_cost_usd=cost_usd)
                outbox.add(
                    "sandbox.cost", "broker", {"party": "e2e", "cost_usd": cost_usd}, "observer"
                )
            outbox.add("e2e.result", "broker", {"passed": passed, "report": sanitized})
            runs = db.exec(
                select(TestRunRecord).where(
                    TestRunRecord.session_id == session_id, TestRunRecord.side == "e2e"
                )
            ).all()
            if passed:
                self.apply_trigger(db, record, Trigger.E2E_PASSED, outbox)
            elif len(runs) >= MAX_E2E_RUNS:
                self.apply_trigger(db, record, Trigger.FAILURE, outbox)
            else:
                feedback = {"report": sanitized, "gateway": self.gateway_traffic(session_id)}
                record.e2e_feedback_json = json.dumps(feedback, ensure_ascii=False, default=str)
                self.apply_trigger(db, record, Trigger.E2E_FAILED, outbox)
            db.add(record)
            db.commit()
        self.flush(session_id, outbox)

    async def autopilot_answer(self, session_id: str, qid: str) -> None:
        async with self.autopilot_locks[session_id]:
            await asyncio.sleep(self.settings.autopilot_delay_s)
            with open_session(self.engine) as db:
                record = self.session_record(db, session_id)
                question = db.get(QuestionRecord, (session_id, qid))
                if question is None or question.answer is not None:
                    return
                role = self.party(db, session_id, question.party_key).role
                preset = self.preset(record.preset)
            answer = await autopilot_answer(
                preset,
                self.simulator,
                question.text,
                json.loads(question.options_json),
                question.default,
                role,
            )
            try:
                self.record_answer(
                    session_id, qid, answer, f"human:{question.party_key}", question.party_key
                )
            except BrokerError as error:
                logger.warning("autopilot could not answer %s: %s", qid, error.message)

    async def autopilot_sign(self, session_id: str) -> None:
        await asyncio.sleep(self.settings.autopilot_delay_s)
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            version = record.current_contract_version
            party_keys = [party.party_key for party in self.roles(db, session_id).values()]
        for party_key in party_keys:
            try:
                self.record_signature(session_id, party_key, version)
            except BrokerError as error:
                logger.warning("autopilot could not sign for %s: %s", party_key, error.message)

    def record_agent_event(
        self,
        principal: Principal,
        session_id: str,
        event: PsmBuiltIn | EgressIn | LlmCallIn | CoderProgressIn | SandboxCostIn,
    ) -> dict[str, Any]:
        self.authorize(principal, session_id, "agent")
        party_key = principal.party_key or ""
        payload = {"party": party_key, **event.model_dump(exclude={"type"})}
        outbox = Outbox()
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            if isinstance(event, PsmBuiltIn | CoderProgressIn):
                outbox.add(event.type, party_key, payload)
            elif isinstance(event, EgressIn):
                counter = "blocked_count" if event.type == "egress.blocked" else "redacted_count"
                self.update_metrics(record, outbox, **{counter: 1})
                outbox.add(event.type, party_key, payload, party_key)
            elif isinstance(event, SandboxCostIn):
                self.update_metrics(record, outbox, sandbox_cost_usd=event.cost_usd)
                outbox.add(event.type, party_key, payload, "observer")
            else:
                self.update_metrics(record, outbox, cost_usd=event.cost_usd, tokens=event.tokens)
                outbox.add(event.type, party_key, payload, "observer")
            db.add(record)
            db.commit()
        self.flush(session_id, outbox)
        return {"accepted": True}

    def events_for(
        self, principal: Principal, session_id: str, since: int = 0
    ) -> list[dict[str, Any]]:
        self.authorize(principal, session_id)
        return [
            event
            for event in self.events.list(session_id, since)
            if principal.can_see(event["visibility"])
        ]

    def audit(self, principal: Principal, session_id: str) -> dict[str, Any]:
        self.authorize(principal, session_id)
        return self.events.verify(session_id).to_dict()

    def certificate(self, principal: Principal, session_id: str) -> dict[str, Any]:
        self.authorize(principal, session_id)
        with open_session(self.engine) as db:
            record = self.session_record(db, session_id)
            runs = db.exec(
                select(TestRunRecord)
                .where(TestRunRecord.session_id == session_id)
                .order_by(col(TestRunRecord.id))
            ).all()
            contract_yaml = (
                self.contract_text(db, session_id, record.signed_contract_version)
                if record.signed_contract_version
                else None
            )
        latest = {run.side: run for run in runs}
        events = self.events.list(session_id)
        gateway = [event["payload"] for event in events if event["type"] == "gateway.request"]
        models: dict[str, float] = defaultdict(float)
        sandboxes: dict[str, float] = defaultdict(float)
        for event in events:
            if event["type"] == "llm.call":
                models[event["payload"]["model"]] += event["payload"]["cost_usd"]
            elif event["type"] == "sandbox.cost":
                sandboxes[event["payload"]["party"]] += event["payload"]["cost_usd"]
        return {
            "session_id": session_id,
            "certified": record.state == State.CERTIFIED.value,
            "state": record.state,
            "signed_contract_version": record.signed_contract_version,
            "contract_sha256": hashlib.sha256(contract_yaml.encode()).hexdigest()
            if contract_yaml
            else None,
            "contract_yaml": contract_yaml,
            "gateway": {
                "requests": len(gateway),
                "refused": sum(1 for item in gateway if not item["allowed"]),
                "schema_violations": sum(len(item.get("violations", [])) for item in gateway),
            },
            "costs": {
                "models": dict(models),
                "sandboxes": dict(sandboxes),
                "total_usd": sum(models.values()) + sum(sandboxes.values()),
            },
            "results": {
                side: {
                    "party": run.party_key,
                    "contract_version": run.contract_version,
                    "summary": json.loads(run.summary_json),
                    "results": json.loads(run.results_json),
                }
                for side, run in latest.items()
            },
            "audit": self.events.verify(session_id).to_dict(),
            "metrics": json.loads(record.metrics_json),
        }

    def gateway_allowlist(self, session_id: str, party_key: str) -> Allowlist | None:
        with open_session(self.engine) as db:
            record = db.get(SessionRecord, session_id)
            if record is None or record.signed_contract_version is None:
                return None
            party = next(
                (p for p in self.roles(db, session_id).values() if p.party_key == party_key), None
            )
            if party is None:
                return None
            contract_yaml = self.contract_text(db, session_id, record.signed_contract_version)
        binding = load_binding_text(party.binding_yaml) if party.binding_yaml else None
        return build_allowlist(load_contract_text(contract_yaml), binding, party.role)

    def gateway_event(self, session_id: str, payload: dict[str, Any]) -> None:
        self.events.append(session_id, "gateway.request", "broker", payload)

    async def gateway_probe(self, session_id: str, party_key: str) -> ProbeResult:
        name = f"gateway request to {party_key} outside the contract"
        try:
            await self.gateway.forward(session_id, party_key, "GET", PROBE_PATH, "", {}, b"")
        except GatewayError as error:
            return ProbeResult(
                name, "403", str(error.status), "passed" if error.status == 403 else "failed"
            )
        return ProbeResult(name, "403", "forwarded", "failed")

    async def run_isolation_probe(self, principal: Principal, session_id: str) -> dict[str, Any]:
        self.authorize(principal, session_id, "observer", "admin")
        with open_session(self.engine) as db:
            parties = [party.party_key for party in self.roles(db, session_id).values()]
        results = [await self.gateway_probe(session_id, party) for party in parties]
        if self.isolation_probe is not None:
            results += await self.isolation_probe.run()
        payload = {
            "ok": all(result.status != "failed" for result in results),
            "results": [result.to_dict() for result in results],
        }
        outbox = Outbox()
        outbox.add("isolation.probe", "broker", payload)
        self.flush(session_id, outbox)
        return payload
