import json
import os
from collections.abc import Callable
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from doubleblind_broker.api import build_gateway_router, build_router
from doubleblind_broker.autopilot import DefaultOptionSimulator, HumanSimulator, LLMHumanSimulator
from doubleblind_broker.db import build_engine
from doubleblind_broker.e2e import E2EExecutor, SandboxGatewayE2E, UnavailableE2E
from doubleblind_broker.gateway import GatewayError
from doubleblind_broker.service import BrokerError, BrokerService, BrokerSettings, IsolationProbe
from doubleblind_sandbox.costs import LedgerCosts
from doubleblind_sandbox.docker import DockerSandboxProvider
from doubleblind_sandbox.isolation_probe import SandboxIsolationProbe
from doubleblind_sandbox.provider import ServiceSandbox
from doubleblind_sandbox.tokenfactory import TokenFactorySandboxProvider

REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_DATABASE = REPO_ROOT / "volumes" / "broker.db"
DASHBOARD_DIST = REPO_ROOT / "dashboard" / "dist"
BASE_IMAGES = {
    "tokenfactory": REPO_ROOT / "volumes" / "base-images.json",
    "docker": REPO_ROOT / "volumes" / "base-images.docker.json",
}


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
    if kind == "docker":
        return DockerSandboxProvider
    if kind == "tokenfactory" and os.environ.get("NEBIUS_API_KEY"):
        return lambda: TokenFactorySandboxProvider.from_env(LedgerCosts.from_env(REPO_ROOT))
    return None


def gateway_url_from_env() -> str | None:
    public = os.environ.get("BROKER_PUBLIC_URL")
    if public:
        return public
    if sandbox_kind() == "docker":
        return f"http://host.docker.internal:{os.environ.get('BROKER_PORT', '8000')}"
    return None


def e2e_executor_from_env() -> E2EExecutor:
    factory, url = sandbox_from_env(), gateway_url_from_env()
    if factory is None or url is None:
        return UnavailableE2E()
    return SandboxGatewayE2E(factory, url)


def isolation_probe_from_env() -> IsolationProbe | None:
    factory = sandbox_from_env()
    images = BASE_IMAGES.get(sandbox_kind())
    if factory is None or images is None or not images.is_file():
        return None
    tags: dict[str, str] = json.loads(images.read_text(encoding="utf-8"))
    return SandboxIsolationProbe(factory, {party: f"tag:{tag}" for party, tag in tags.items()})


def create_app(
    service: BrokerService | None = None,
    database_url: str | None = None,
    settings: BrokerSettings | None = None,
    e2e_executor: E2EExecutor | None = None,
    simulator: HumanSimulator | None = None,
) -> FastAPI:
    if service is None:
        url = database_url or os.environ.get(
            "DOUBLEBLIND_BROKER_DB", f"sqlite:///{DEFAULT_DATABASE.as_posix()}"
        )
        if url.startswith("sqlite:///") and url not in ("sqlite:///:memory:",):
            Path(url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)
        service = BrokerService(
            build_engine(url),
            settings or settings_from_env(),
            e2e_executor or e2e_executor_from_env(),
            simulator or simulator_from_env(),
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

    app.include_router(build_router(service))
    app.include_router(build_gateway_router(service))
    if DASHBOARD_DIST.is_dir():
        app.mount("/", StaticFiles(directory=DASHBOARD_DIST, html=True), name="dashboard")
    return app
