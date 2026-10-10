import asyncio
import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine
from sqlmodel import col, select

from doubleblind_broker.db import EventRecord, open_session

GENESIS = "GENESIS"


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def event_hash(prev_hash: str, type_: str, actor: str, payload_json: str, ts: str) -> str:
    return hashlib.sha256(f"{prev_hash}{type_}{actor}{payload_json}{ts}".encode()).hexdigest()


def event_view(record: EventRecord) -> dict[str, Any]:
    return {
        "seq": record.seq,
        "session_id": record.session_id,
        "ts": record.ts,
        "type": record.type,
        "actor": record.actor,
        "visibility": record.visibility,
        "payload": json.loads(record.payload_json),
        "hash": record.hash,
    }


@dataclass(frozen=True)
class AuditResult:
    ok: bool
    events: int
    last_hash: str
    broken_at: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "events": self.events,
            "last_hash": self.last_hash,
            "broken_at": self.broken_at,
        }


class EventLog:
    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.subscribers: dict[str, set[asyncio.Queue[dict[str, Any]]]] = defaultdict(set)

    def last_hash(self, session_id: str) -> str:
        with open_session(self.engine) as db:
            last = db.exec(
                select(EventRecord)
                .where(EventRecord.session_id == session_id)
                .order_by(col(EventRecord.seq).desc())
                .limit(1)
            ).first()
        return last.hash if last is not None else GENESIS

    def append(
        self,
        session_id: str,
        type_: str,
        actor: str,
        payload: dict[str, Any],
        visibility: str = "shared",
    ) -> dict[str, Any]:
        payload_json = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        ts = utc_now()
        prev_hash = self.last_hash(session_id)
        record = EventRecord(
            session_id=session_id,
            ts=ts,
            type=type_,
            actor=actor,
            visibility=visibility,
            payload_json=payload_json,
            prev_hash=prev_hash,
            hash=event_hash(prev_hash, type_, actor, payload_json, ts),
        )
        with open_session(self.engine) as db:
            db.add(record)
            db.commit()
            db.refresh(record)
        view = event_view(record)
        for queue in list(self.subscribers[session_id]):
            queue.put_nowait(view)
        return view

    def list(self, session_id: str, since: int = 0) -> list[dict[str, Any]]:
        with open_session(self.engine) as db:
            records = db.exec(
                select(EventRecord)
                .where(EventRecord.session_id == session_id, col(EventRecord.seq) > since)
                .order_by(col(EventRecord.seq))
            ).all()
        return [event_view(record) for record in records]

    def verify(self, session_id: str) -> AuditResult:
        with open_session(self.engine) as db:
            records = db.exec(
                select(EventRecord)
                .where(EventRecord.session_id == session_id)
                .order_by(col(EventRecord.seq))
            ).all()
        previous = GENESIS
        for record in records:
            expected = event_hash(
                previous, record.type, record.actor, record.payload_json, record.ts
            )
            if record.prev_hash != previous or record.hash != expected:
                return AuditResult(False, len(records), previous, record.seq)
            previous = record.hash
        return AuditResult(True, len(records), previous)

    def subscribe(self, session_id: str) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self.subscribers[session_id].add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self.subscribers[session_id].discard(queue)
