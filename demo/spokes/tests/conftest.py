import os
from collections.abc import Callable, Iterator

import pytest
from fastapi.testclient import TestClient

os.environ["SPOKES_DATABASE_URL"] = "sqlite://"


@pytest.fixture
def client() -> Iterator[TestClient]:
    from app.db import reset_db
    from app.main import app

    reset_db()
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def paid_handlers() -> Iterator[list[Callable[..., None]]]:
    from app.services.order_events import ORDER_PAID_HANDLERS

    registered = list(ORDER_PAID_HANDLERS)
    ORDER_PAID_HANDLERS.clear()
    yield ORDER_PAID_HANDLERS
    ORDER_PAID_HANDLERS.clear()
    ORDER_PAID_HANDLERS.extend(registered)
