import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Cookie, Depends, Header, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response
from pydantic import BaseModel, ConfigDict, Field
from sse_starlette.sse import EventSourceResponse

from doubleblind_broker.accounts import LOGIN_TTL_S, Accounts
from doubleblind_broker.certificate import render_certificate
from doubleblind_broker.integrations import Integrations
from doubleblind_broker.service import (
    AgentEventIn,
    BrokerService,
    ContractGapIn,
    TestRunIn,
    TurnIn,
)
from doubleblind_broker.tokens import Principal

STREAM_POLL_S = 15.0
LOGIN_COOKIE = "doubleblind_login"
EMAIL = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
LoginToken = Annotated[str | None, Cookie(alias=LOGIN_COOKIE)]


class SignupIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(pattern=EMAIL, max_length=254)
    password: str = Field(min_length=10, max_length=200)
    company: str = Field(min_length=1, max_length=80)


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(max_length=254)
    password: str = Field(max_length=200)


class CreateIntegrationIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    role: Literal["consumer", "provider"]


class CreateSessionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    preset: str = Field(min_length=1)


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


def build_router(
    service: BrokerService, accounts: Accounts, integrations: Integrations
) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    async def principal(
        request: Request,
        authorization: Annotated[str | None, Header()] = None,
        token: Annotated[str | None, Query()] = None,
        login: LoginToken = None,
    ) -> Principal:
        supplied = bearer(authorization) or token
        session_id = request.path_params.get("session_id")
        if supplied or login is None or session_id is None:
            return service.resolve(supplied)
        return integrations.session_principal(accounts.account(login)["id"], session_id)

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
        return service.create_session(caller, body.name, body.preset)

    @router.get("/public/sessions")
    async def public_sessions() -> list[dict[str, Any]]:
        return service.public_sessions()

    @router.post("/sessions/{session_id}/publish")
    async def publish(session_id: str, caller: Caller) -> dict[str, Any]:
        return service.publish(caller, session_id)

    @router.post("/sessions/{session_id}/start")
    async def start(session_id: str, caller: Caller) -> dict[str, Any]:
        return service.start(caller, session_id)

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
        return service.submit_brief(caller, session_id, brief)

    @router.get("/sessions/{session_id}/briefs/counterparty")
    async def counterparty_brief(session_id: str, caller: Caller) -> dict[str, Any]:
        return service.counterparty_brief(caller, session_id)

    @router.post("/sessions/{session_id}/turns", status_code=201)
    async def submit_turn(session_id: str, turn: TurnIn, caller: Caller) -> dict[str, Any]:
        return service.submit_turn(caller, session_id, turn)

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

    @router.post("/questions/{qid}/answer")
    async def answer(qid: str, body: AnswerIn, caller: Caller) -> dict[str, Any]:
        return service.answer_question(caller, qid, body.answer)

    @router.post("/sessions/{session_id}/questions/{qid}/answer")
    async def answer_in_session(
        session_id: str, qid: str, body: AnswerIn, caller: Caller
    ) -> dict[str, Any]:
        service.authorize(caller, session_id, "human")
        return service.answer_question(caller, qid, body.answer)

    @router.post("/sessions/{session_id}/resume")
    async def resume(session_id: str, body: ResumeIn, caller: Caller) -> dict[str, Any]:
        return service.resume(caller, session_id, body.extra_rounds)

    @router.post("/sessions/{session_id}/signatures", status_code=201)
    async def sign(session_id: str, body: SignatureIn, caller: Caller) -> dict[str, Any]:
        return service.sign(caller, session_id, body.version)

    @router.post("/sessions/{session_id}/test-runs", status_code=201)
    async def test_run(session_id: str, run: TestRunIn, caller: Caller) -> dict[str, Any]:
        return service.submit_test_run(caller, session_id, run)

    @router.post("/sessions/{session_id}/contract-gap", status_code=201)
    async def contract_gap(session_id: str, gap: ContractGapIn, caller: Caller) -> dict[str, Any]:
        return service.contract_gap(caller, session_id, gap)

    @router.post("/sessions/{session_id}/e2e/run", status_code=202)
    async def run_e2e(session_id: str, caller: Caller) -> dict[str, Any]:
        return service.trigger_e2e(caller, session_id)

    @router.post("/sessions/{session_id}/events", status_code=201)
    async def agent_event(session_id: str, event: AgentEventIn, caller: Caller) -> dict[str, Any]:
        return service.record_agent_event(caller, session_id, event)

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

    @router.get("/sessions/{session_id}/audit/verify")
    async def audit(session_id: str, caller: Caller) -> dict[str, Any]:
        return service.audit(caller, session_id)

    @router.get("/sessions/{session_id}/certificate", response_model=None)
    async def certificate(
        session_id: str, caller: Caller, format: Literal["json", "html"] = "json"
    ) -> dict[str, Any] | HTMLResponse:
        issued = service.certificate(caller, session_id)
        return HTMLResponse(render_certificate(issued)) if format == "html" else issued

    @router.post("/sessions/{session_id}/isolation-probe")
    async def isolation_probe(session_id: str, caller: Caller) -> dict[str, Any]:
        return await service.run_isolation_probe(caller, session_id)

    return router


def build_account_router(accounts: Accounts, integrations: Integrations) -> APIRouter:
    router = APIRouter(prefix="/api/v1")

    def account_id(login: LoginToken = None) -> str:
        return str(accounts.account(login)["id"])

    Member = Annotated[str, Depends(account_id)]

    def signed_in(
        request: Request, response: Response, signed: tuple[dict[str, Any], str]
    ) -> dict[str, Any]:
        account, token = signed
        secure = "https" in (request.url.scheme, request.headers.get("x-forwarded-proto"))
        response.set_cookie(
            LOGIN_COOKIE,
            token,
            max_age=LOGIN_TTL_S,
            httponly=True,
            samesite="lax",
            secure=secure,
        )
        return account

    @router.post("/auth/signup", status_code=201)
    def signup(body: SignupIn, request: Request, response: Response) -> dict[str, Any]:
        return signed_in(
            request, response, accounts.signup(body.email, body.password, body.company)
        )

    @router.post("/auth/login")
    def login(body: LoginIn, request: Request, response: Response) -> dict[str, Any]:
        return signed_in(request, response, accounts.login(body.email, body.password))

    @router.post("/auth/logout", status_code=204)
    def logout(login: LoginToken = None) -> Response:
        accounts.logout(login)
        response = Response(status_code=204)
        response.delete_cookie(LOGIN_COOKIE)
        return response

    @router.get("/auth/me")
    def me(login: LoginToken = None) -> dict[str, Any]:
        return accounts.account(login)

    @router.post("/integrations", status_code=201)
    def create_integration(body: CreateIntegrationIn, member: Member) -> dict[str, Any]:
        return integrations.create(member, body.name, body.role)

    @router.get("/integrations")
    def list_integrations(member: Member) -> list[dict[str, Any]]:
        return integrations.list(member)

    @router.get("/integrations/{integration_id}")
    def integration(integration_id: str, member: Member) -> dict[str, Any]:
        return integrations.get(member, integration_id)

    @router.post("/integrations/{integration_id}/invite")
    def renew_invite(integration_id: str, member: Member) -> dict[str, Any]:
        return integrations.renew_invite(member, integration_id)

    @router.post("/integrations/{integration_id}/agent-key")
    def issue_agent_key(integration_id: str, member: Member) -> dict[str, str]:
        return integrations.issue_agent_key(member, integration_id)

    @router.delete("/integrations/{integration_id}/agent-key", status_code=204)
    def revoke_agent_key(integration_id: str, member: Member) -> Response:
        integrations.revoke_agent_key(member, integration_id)
        return Response(status_code=204)

    @router.get("/invites/{token}")
    def invite(token: str) -> dict[str, Any]:
        return integrations.invite(token)

    @router.post("/invites/{token}/accept")
    def accept_invite(token: str, member: Member) -> dict[str, Any]:
        return integrations.accept(member, token)

    return router


def build_gateway_router(service: BrokerService) -> APIRouter:
    router = APIRouter()
    gateway = service.gateway

    @router.api_route(
        "/gw/{session_id}/{party}/{path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
    )
    async def forward(session_id: str, party: str, path: str, request: Request) -> Response:
        relayed = await gateway.forward(
            session_id,
            party,
            request.method,
            f"/{path}",
            request.url.query,
            dict(request.headers),
            await request.body(),
        )
        content_type = next(
            (value for key, value in relayed.headers.items() if key.lower() == "content-type"),
            None,
        )
        return Response(relayed.body, relayed.status, media_type=content_type)

    @router.get("/relay/{session_id}/{party}/next")
    async def next_request(
        session_id: str,
        party: str,
        x_relay_token: Annotated[str | None, Header()] = None,
        wait: Annotated[float, Query(ge=0, le=60)] = 25,
    ) -> Response:
        item = await gateway.next_request(session_id, party, x_relay_token, wait)
        if item is None:
            return Response(status_code=204)
        return JSONResponse(item)

    @router.post("/relay/{session_id}/{party}/responses/{request_id}")
    async def relay_response(
        session_id: str,
        party: str,
        request_id: str,
        body: dict[str, Any],
        x_relay_token: Annotated[str | None, Header()] = None,
    ) -> dict[str, str]:
        gateway.respond(session_id, party, x_relay_token, request_id, body)
        return {"status": "delivered"}

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
    return {"id": str(event["seq"]), "data": json.dumps(event, ensure_ascii=False)}
