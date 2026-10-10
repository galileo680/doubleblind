import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app import testhooks
from app.db import init_db
from app.integrations.parcelly import webhook
from app.integrations.parcelly.handler import register_handler
from app.routes import orders

TEST_HOOKS_ENV = "DOUBLEBLIND_TEST_HOOKS"


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    init_db()
    yield


def create_app() -> FastAPI:
    application = FastAPI(title="Spokes", version="1.5.0", lifespan=lifespan)
    application.include_router(orders.router)
    application.include_router(webhook.router)
    if os.environ.get(TEST_HOOKS_ENV) == "1":
        application.include_router(testhooks.router)

    @application.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return application


register_handler()
app = create_app()
