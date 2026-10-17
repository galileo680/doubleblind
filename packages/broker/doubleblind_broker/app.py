import os
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from doubleblind_broker.accounts import Accounts
from doubleblind_broker.api import build_account_router, build_gateway_router, build_router
from doubleblind_broker.autopilot import DefaultOptionSimulator, HumanSimulator, LLMHumanSimulator
from doubleblind_broker.db import build_engine
from doubleblind_broker.e2e import E2EExecutor, SandboxGatewayE2E, UnavailableE2E
from doubleblind_broker.gateway import GatewayError
from doubleblind_broker.integrations import Integrations
from doubleblind_broker.service import BrokerError, BrokerService, BrokerSettings, IsolationProbe
from doubleblind_sandbox.environment import gateway_url, recorded_bases, remote_sandbox
from doubleblind_sandbox.isolation_probe import SandboxIsolationProbe
from doubleblind_sandbox.provider import ServiceSandbox

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE = REPO_ROOT / "volumes" / "broker.db"
DASHBOARD_DIST = REPO_ROOT / "dashboard" / "dist"


def settings_from_env() -> BrokerSettings:
    return BrokerSettings(
        presets_dir=Path(os.environ.get("DOUBLEBLIND_PRESETS_DIR", REPO_ROOT / "demo" / "presets")),
        max_rounds=int(os.environ.get("DOUBLEBLIND_MAX_ROUNDS", "14")),
        autopilot=os.environ.get("DOUBLEBLIND_AUTOPILOT", "0") == "1",
        autopilot_delay_s=int(os.environ.get("DOUBLEBLIND_AUTOPILOT_DELAY_MS", "1500")) / 1000,
        admin_token=os.environ.get("DOUBLEBLIND_ADMIN_TOKEN") or None,
    )


def simulator_from_env() -> HumanSimulator:
    if not os.environ.get("NEBIUS_API_KEY"):
        return DefaultOptionSimulator()
    from doubleblind_llm import LLMClient

    return LLMHumanSimulator(LLMClient.from_env())


def sandbox_kind() -> str:
    return os.environ.get("DOUBLEBLIND_SANDBOX", "tokenfactory")


def sandbox_from_env() -> Callable[[], ServiceSandbox] | None:
    kind = sandbox_kind()
    if kind == "docker" or (kind == "tokenfactory" and os.environ.get("NEBIUS_API_KEY")):
        return remote_sandbox(kind)
    return None


def e2e_executor_from_env() -> E2EExecutor:
    factory = sandbox_from_env()
    url = gateway_url(sandbox_kind(), os.environ.get("BROKER_PORT", "8000"))
    if factory is None or url is None:
        return UnavailableE2E()
    return SandboxGatewayE2E(factory, url)


def isolation_probe_from_env() -> IsolationProbe | None:
    factory, bases = sandbox_from_env(), recorded_bases(sandbox_kind())
    if factory is None or bases is None:
        return None
    return SandboxIsolationProbe(factory, bases)


def create_app(service: BrokerService | None = None) -> FastAPI:
    if service is None:
        url = os.environ.get("DOUBLEBLIND_BROKER_DB", f"sqlite:///{DEFAULT_DATABASE.as_posix()}")
        if url.startswith("sqlite:///") and url != "sqlite:///:memory:":
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        service = BrokerService(
            build_engine(url),
            settings_from_env(),
            e2e_executor_from_env(),
            simulator_from_env(),
            isolation_probe_from_env(),
        )
    app = FastAPI(title="DoubleBlind broker", version="0.1.0")
    app.state.service = service

    @app.exception_handler(BrokerError)
    async def broker_error(_: Request, error: BrokerError) -> JSONResponse:
        return JSONResponse(status_code=error.status, content=error.to_dict())

    @app.exception_handler(GatewayError)
    async def gateway_error(_: Request, error: GatewayError) -> JSONResponse:
        return JSONResponse(
            status_code=error.status, content={"code": error.code, "message": error.message}
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    accounts, integrations = Accounts(service.engine), Integrations(service.engine)
    app.include_router(build_router(service, accounts, integrations))
    app.include_router(build_account_router(accounts, integrations))
    app.include_router(build_gateway_router(service))
    if DASHBOARD_DIST.is_dir():
        app.mount("/", StaticFiles(directory=DASHBOARD_DIST, html=True), name="dashboard")
    return app
