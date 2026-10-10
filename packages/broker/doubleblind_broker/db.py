import json
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine
from sqlalchemy.pool import StaticPool
from sqlmodel import Field, Session, SQLModel, create_engine

IN_MEMORY_URLS = frozenset({"sqlite://", "sqlite:///:memory:"})


class SessionRecord(SQLModel, table=True):
    __tablename__ = "sessions"

    id: str = Field(primary_key=True)
    name: str
    preset: str
    state: str
    round: int = 0
    max_rounds: int
    turn_party: str | None = None
    created_at: str
    updated_at: str
    consumer_party_id: str
    provider_party_id: str
    current_contract_version: int = 1
    signed_contract_version: int | None = None
    observer_token_hash: str = Field(index=True)
    secrets_json: str = "{}"
    metrics_json: str = json.dumps(
        {"cost_usd": 0.0, "tokens": 0, "code_lines_shared": 0, "blocked_count": 0}
    )
    e2e_feedback_json: str | None = None


class PartyRecord(SQLModel, table=True):
    __tablename__ = "parties"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    party_key: str
    role: str
    display_name: str
    agent_token_hash: str = Field(index=True)
    human_token_hash: str = Field(index=True)
    status: str = "waiting"
    binding_yaml: str | None = None
    workspace_image: str | None = None
    brief_json: str | None = None
    last_turn_status: str | None = None
    last_accepted_version: int | None = None
    last_turn_had_patches: bool = False


class EventRecord(SQLModel, table=True):
    __tablename__ = "events"

    seq: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    ts: str
    type: str
    actor: str
    visibility: str
    payload_json: str
    prev_hash: str
    hash: str


class ContractVersionRecord(SQLModel, table=True):
    __tablename__ = "contract_versions"

    session_id: str = Field(primary_key=True)
    version: int = Field(primary_key=True)
    yaml_text: str
    created_by: str
    created_at: str


class TurnRecord(SQLModel, table=True):
    __tablename__ = "turns"

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    round: int
    party_key: str
    based_on_version: int
    message: str
    patches_json: str
    status: str
    accepted: bool
    errors_json: str = "[]"
    contract_version_after: int | None = None


class QuestionRecord(SQLModel, table=True):
    __tablename__ = "questions"

    session_id: str = Field(primary_key=True)
    id: str = Field(primary_key=True)
    party_key: str
    text: str
    options_json: str = "[]"
    default: str | None = None
    answer: str | None = None
    answered_by: str | None = None
    decision_id: str | None = None
    created_at: str


class SignatureRecord(SQLModel, table=True):
    __tablename__ = "signatures"

    session_id: str = Field(primary_key=True)
    party_key: str = Field(primary_key=True)
    version: int = Field(primary_key=True)
    signed_at: str


class TestRunRecord(SQLModel, table=True):
    __tablename__ = "test_runs"
    __test__ = False

    id: int | None = Field(default=None, primary_key=True)
    session_id: str = Field(index=True)
    party_key: str
    side: str
    contract_version: int
    summary_json: str
    results_json: str
    created_at: str


def build_engine(url: str) -> Engine:
    if url in IN_MEMORY_URLS:
        engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    else:
        engine = create_engine(url, connect_args={"check_same_thread": False})
    SQLModel.metadata.create_all(engine)
    return engine


@contextmanager
def open_session(engine: Engine) -> Iterator[Session]:
    with Session(engine, expire_on_commit=False) as session:
        yield session
