import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from doubleblind_broker.api import build_router
from doubleblind_broker.autopilot import DefaultOptionSimulator, HumanSimulator, LLMHumanSimulator
from doubleblind_broker.db import build_engine
from doubleblind_broker.e2e import E2EExecutor
from doubleblind_broker.service import BrokerError, BrokerService, BrokerSettings

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
            e2e_executor,
            simulator or simulator_from_env(),
        )
    app = FastAPI(title="DoubleBlind broker", version="0.1.0")
    app.state.service = service

    @app.exception_handler(BrokerError)
    async def broker_error(_: Request, error: BrokerError) -> JSONResponse:
        return JSONResponse(status_code=error.status, content=error.to_dict())

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(build_router(service))
    if DASHBOARD_DIST.is_dir():
        app.mount("/", StaticFiles(directory=DASHBOARD_DIST, html=True), name="dashboard")
    return app
