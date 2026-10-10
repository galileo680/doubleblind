import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from doubleblind_broker.service import (
    BrokerError,
    BrokerService,
    ContractGapIn,
    TestRunIn,
    TurnIn,
)
from doubleblind_broker.tokens import Principal

STREAM_POLL_S = 15.0


class CreateSessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    preset: str = Field(min_length=1)
    max_rounds: int | None = Field(default=None, ge=2)


class AnswerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: str = Field(min_length=1)


class SignatureIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)


class ResumeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    extra_rounds: int = Field(default=4, ge=1, le=20)


def bearer(value: str | None) -> str | None:
    if value and value.lower().startswith("bearer "):
        return value[7:].strip()
    return None


def build_router(service: BrokerService) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    async def principal(
        authorization: Annotated[str | None, Header()] = None,
        token: Annotated[str | None, Query()] = None,
    ) -> Principal:
        return service.resolve(bearer(authorization) or token)

    async def optional_principal(
        authorization: Annotated[str | None, Header()] = None,
    ) -> Principal:
        token = bearer(authorization)
        if token is None and not service.settings.admin_token:
            return Principal("admin")
        return service.resolve(token)

    Caller = Annotated[Principal, Depends(principal)]

    @router.post("/sessions", status_code=201)
    async def create_session(
        body: CreateSessionIn, caller: Annotated[Principal, Depends(optional_principal)]
    ) -> dict[str, Any]:
        return await service.create_session(caller, body.name, body.preset, body.max_rounds)

    @router.post("/sessions/{session_id}/start")
    async def start(session_id: str, caller: Caller) -> dict[str, Any]:
        return await service.start(caller, session_id)

    @router.get("/sessions/{session_id}")
    async def session_view(session_id: str, caller: Caller) -> dict[str, Any]:
        return service.session_view(caller, session_id)

    @router.get("/sessions/{session_id}/next-action")
    async def next_action(
        session_id: str, caller: Caller, wait: Annotated[float | None, Query(ge=0, le=60)] = None
    ) -> dict[str, Any]:
        return await service.next_action(caller, session_id, wait)

    @router.post("/sessions/{session_id}/briefs", status_code=201)
    async def submit_brief(
        session_id: str, brief: dict[str, Any], caller: Caller
    ) -> dict[str, Any]:
        return await service.submit_brief(caller, session_id, brief)

    @router.get("/sessions/{session_id}/briefs/mine")
    async def own_brief(session_id: str, caller: Caller) -> dict[str, Any]:
        return service.own_brief(caller, session_id, counterparty=False)

    @router.get("/sessions/{session_id}/briefs/counterparty")
    async def counterparty_brief(session_id: str, caller: Caller) -> dict[str, Any]:
        return service.own_brief(caller, session_id, counterparty=True)

    @router.post("/sessions/{session_id}/turns", status_code=201)
    async def submit_turn(session_id: str, turn: TurnIn, caller: Caller) -> dict[str, Any]:
        return await service.submit_turn(caller, session_id, turn)

    @router.get("/sessions/{session_id}/turns")
    async def list_turns(session_id: str, caller: Caller) -> list[dict[str, Any]]:
        return service.list_turns(caller, session_id)

    @router.get("/sessions/{session_id}/contract", response_class=PlainTextResponse)
    async def contract(
        session_id: str, caller: Caller, version: Annotated[int | None, Query(ge=1)] = None
    ) -> str:
        return service.get_contract(caller, session_id, version)

    @router.get("/sessions/{session_id}/contract/diff", response_class=PlainTextResponse)
    async def contract_diff(
        session_id: str,
        caller: Caller,
        old: Annotated[int, Query(alias="from", ge=1)],
        new: Annotated[int, Query(alias="to", ge=1)],
    ) -> str:
        return service.contract_diff(caller, session_id, old, new)

    @router.get("/sessions/{session_id}/questions")
    async def questions(session_id: str, caller: Caller) -> list[dict[str, Any]]:
        return service.list_questions(caller, session_id)

    @router.post("/sessions/{session_id}/questions/{qid}/answer")
    async def answer_in_session(
        session_id: str, qid: str, body: AnswerIn, caller: Caller
    ) -> dict[str, Any]:
        return await service.answer_question(caller, session_id, qid, body.answer)

    @router.post("/questions/{qid}/answer")
    async def answer(qid: str, body: AnswerIn, caller: Caller) -> dict[str, Any]:
        if caller.session_id is None:
            raise BrokerError(400, "SESSION_REQUIRED", "use /sessions/{id}/questions/{qid}/answer")
        return await service.answer_question(caller, caller.session_id, qid, body.answer)

    @router.post("/sessions/{session_id}/resume")
    async def resume(session_id: str, body: ResumeIn, caller: Caller) -> dict[str, Any]:
        return await service.resume(caller, session_id, body.extra_rounds)

    @router.post("/sessions/{session_id}/signatures", status_code=201)
    async def sign(session_id: str, body: SignatureIn, caller: Caller) -> dict[str, Any]:
        return await service.sign(caller, session_id, body.version)

    @router.post("/sessions/{session_id}/test-runs", status_code=201)
    async def test_run(session_id: str, run: TestRunIn, caller: Caller) -> dict[str, Any]:
        return await service.submit_test_run(caller, session_id, run)

    @router.post("/sessions/{session_id}/contract-gap", status_code=201)
    async def contract_gap(session_id: str, gap: ContractGapIn, caller: Caller) -> dict[str, Any]:
        return await service.contract_gap(caller, session_id, gap)

    @router.post("/sessions/{session_id}/e2e/run", status_code=202)
    async def run_e2e(session_id: str, caller: Caller) -> dict[str, Any]:
        return await service.trigger_e2e(caller, session_id)

    @router.get("/sessions/{session_id}/events")
    async def events(
        session_id: str, caller: Caller, since: Annotated[int, Query(ge=0)] = 0
    ) -> list[dict[str, Any]]:
        return service.events_for(caller, session_id, since)

    @router.get("/sessions/{session_id}/events/stream")
    async def event_stream(
        session_id: str,
        request: Request,
        caller: Caller,
        since: Annotated[int, Query(ge=0)] = 0,
        follow: bool = True,
    ) -> EventSourceResponse:
        service.authorize(caller, session_id)
        return EventSourceResponse(stream(service, request, caller, session_id, since, follow))

    @router.post("/sessions/{session_id}/isolation-probe")
    async def isolation_probe(session_id: str, caller: Caller) -> dict[str, Any]:
        service.authorize(caller, session_id, "observer", "admin")
        raise BrokerError(501, "NOT_AVAILABLE", "isolation probes arrive with the sandboxes (M6)")

    @router.get("/sessions/{session_id}/audit/verify")
    async def audit(session_id: str, caller: Caller) -> dict[str, Any]:
        return service.audit(caller, session_id)

    @router.get("/sessions/{session_id}/certificate")
    async def certificate(session_id: str, caller: Caller) -> dict[str, Any]:
        return service.certificate(caller, session_id)

    return router


async def stream(
    service: BrokerService,
    request: Request,
    caller: Principal,
    session_id: str,
    since: int,
    follow: bool,
) -> AsyncIterator[dict[str, str]]:
    queue = service.events.subscribe(session_id)
    try:
        last_seq = since
        for event in service.events.list(session_id, since):
            last_seq = event["seq"]
            if caller.can_see(event["visibility"]):
                yield sse_message(event)
        while follow and not await request.is_disconnected():
            try:
                event = await asyncio.wait_for(queue.get(), STREAM_POLL_S)
            except TimeoutError:
                continue
            if event["seq"] <= last_seq:
                continue
            last_seq = event["seq"]
            if caller.can_see(event["visibility"]):
                yield sse_message(event)
    finally:
        service.events.unsubscribe(session_id, queue)


def sse_message(event: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(event["seq"]),
        "event": event["type"],
        "data": json.dumps(event, ensure_ascii=False),
    }
