import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

from sqlmodel import Session, SQLModel

from doubleblind_broker.db import (
    ContractVersionRecord,
    EventRecord,
    PartyRecord,
    QuestionRecord,
    SessionRecord,
    SignatureRecord,
    TestRunRecord,
    TurnRecord,
)
from doubleblind_broker.events import GENESIS, event_hash
from doubleblind_broker.tokens import hash_token, new_token

RECORDED_RUN = Path(__file__).resolve().parents[3] / "demo" / "recordings" / "certified-run.json"
TABLES: tuple[type[SQLModel], ...] = (
    SessionRecord,
    PartyRecord,
    EventRecord,
    ContractVersionRecord,
    TurnRecord,
    QuestionRecord,
    SignatureRecord,
    TestRunRecord,
)


def stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="microseconds").replace("+00:00", "Z")


def add_recorded_run(db: Session, speed: int) -> str:
    text = RECORDED_RUN.read_text(encoding="utf-8")
    session_id = f"ses_{secrets.token_hex(6)}"
    tables = json.loads(text.replace(json.loads(text)["sessions"][0]["id"], session_id))
    start = datetime.now(UTC)
    first = datetime.fromisoformat(tables["events"][0]["ts"])
    previous = GENESIS
    for event in tables["events"]:
        ts = stamp(start + (datetime.fromisoformat(event["ts"]) - first) / speed)
        event.update(
            ts=ts,
            prev_hash=previous,
            hash=event_hash(previous, event["type"], event["actor"], event["payload_json"], ts),
        )
        previous = event["hash"]
    tables["sessions"][0].update(
        created_at=stamp(start),
        updated_at=stamp(start),
        observer_token_hash=hash_token(new_token()),
    )
    for party in tables["parties"]:
        party.update(
            agent_token_hash=hash_token(new_token()), human_token_hash=hash_token(new_token())
        )
    for model in TABLES:
        db.add_all(model(**row) for row in tables[str(model.__tablename__)])
    return session_id
