from collections.abc import Callable
from typing import Any

from fastapi.testclient import TestClient

from app.db import SessionLocal
from app.models import Order

CITY_BIKE = {
    "sku": "CITY-U3",
    "name": "Spokes Urban 3 city bike",
    "quantity": 1,
    "unit_price_pence": 289900,
    "unit_weight_kg": 14.5,
}
LIGHT_SET = {
    "sku": "LIGHTS-SET",
    "name": "Front and rear light set",
    "quantity": 2,
    "unit_price_pence": 12900,
    "unit_weight_kg": 0.3,
}


def order_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "customer_name": "John Smith",
        "phone": "07700 900200",
        "address_line": "Flat 3, 5 Long Street, London E1 6AN",
        "items": [CITY_BIKE],
    }
    payload.update(overrides)
    return payload


def create_order(client: TestClient, **overrides: Any) -> dict[str, Any]:
    response = client.post("/orders", json=order_payload(**overrides))
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def set_status(order_id: str, status: str) -> None:
    with SessionLocal() as session:
        order = session.get(Order, order_id)
        assert order is not None
        order.status = status
        session.commit()


def test_health(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_create_order_numbers_orders_and_sums_items(client: TestClient) -> None:
    first = create_order(client)
    second = create_order(client, items=[CITY_BIKE, LIGHT_SET])

    assert first["id"] == "ORD-1001"
    assert first["status"] == "pending"
    assert first["paid_at"] is None
    assert second["id"] == "ORD-1002"
    assert second["weight_kg"] == 15.1
    assert second["total_pence"] == 315700
    assert [item["sku"] for item in second["items"]] == ["CITY-U3", "LIGHTS-SET"]


def test_create_order_keeps_customer_input_as_typed(client: TestClient) -> None:
    order = create_order(
        client,
        phone=" 0044 7700-900200 ",
        address_line="12 St John's Wood Road, Apt 4, London NW8 8RX",
    )

    assert order["phone"] == "0044 7700-900200"
    assert order["address_line"] == "12 St John's Wood Road, Apt 4, London NW8 8RX"


def test_create_order_validates_payload(client: TestClient) -> None:
    assert client.post("/orders", json=order_payload(items=[])).status_code == 422
    payload = order_payload()
    del payload["phone"]
    assert client.post("/orders", json=payload).status_code == 422


def test_read_order(client: TestClient) -> None:
    created = create_order(client)

    response = client.get(f"/orders/{created['id']}")

    assert response.status_code == 200
    assert response.json()["customer_name"] == "John Smith"


def test_read_unknown_order_returns_404(client: TestClient) -> None:
    response = client.get("/orders/ORD-9999")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "ORDER_NOT_FOUND"


def test_list_orders_filters_by_status(client: TestClient) -> None:
    first = create_order(client)
    create_order(client)
    client.post(f"/orders/{first['id']}/pay")

    all_orders = client.get("/orders").json()
    paid = client.get("/orders", params={"status": "paid"}).json()

    assert [order["id"] for order in all_orders] == ["ORD-1001", "ORD-1002"]
    assert [order["id"] for order in paid] == ["ORD-1001"]


def test_pay_order_marks_order_paid_and_calls_handlers(
    client: TestClient, paid_handlers: list[Callable[[Order], None]]
) -> None:
    seen: list[tuple[str, str]] = []
    paid_handlers.append(lambda order: seen.append((order.id, order.status)))
    created = create_order(client)

    response = client.post(f"/orders/{created['id']}/pay")

    assert response.status_code == 200
    assert response.json()["status"] == "paid"
    assert response.json()["paid_at"] is not None
    assert seen == [("ORD-1001", "paid")]


def test_duplicate_payment_notification_calls_handlers_again(
    client: TestClient, paid_handlers: list[Callable[[Order], None]]
) -> None:
    seen: list[str] = []
    paid_handlers.append(lambda order: seen.append(order.id))
    created = create_order(client)

    first = client.post(f"/orders/{created['id']}/pay").json()
    second = client.post(f"/orders/{created['id']}/pay").json()

    assert seen == ["ORD-1001", "ORD-1001"]
    assert second["paid_at"] == first["paid_at"]


def test_failing_handler_does_not_undo_payment(
    client: TestClient, paid_handlers: list[Callable[[Order], None]]
) -> None:
    seen: list[str] = []

    def broken(order: Order) -> None:
        raise RuntimeError("carrier unavailable")

    paid_handlers.extend([broken, lambda order: seen.append(order.id)])
    created = create_order(client)

    response = client.post(f"/orders/{created['id']}/pay")

    assert response.status_code == 200
    assert response.json()["status"] == "paid"
    assert seen == ["ORD-1001"]


def test_pay_returns_changes_made_by_handlers(
    client: TestClient, paid_handlers: list[Callable[[Order], None]]
) -> None:
    paid_handlers.append(lambda order: set_status(order.id, "shipped"))
    created = create_order(client)

    response = client.post(f"/orders/{created['id']}/pay")

    assert response.json()["status"] == "shipped"


def test_cancelled_order_cannot_be_paid(
    client: TestClient, paid_handlers: list[Callable[[Order], None]]
) -> None:
    seen: list[str] = []
    paid_handlers.append(lambda order: seen.append(order.id))
    created = create_order(client)
    set_status(created["id"], "cancelled")

    response = client.post(f"/orders/{created['id']}/pay")

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "ORDER_NOT_PAYABLE"
    assert seen == []


def test_pay_unknown_order_returns_404(client: TestClient) -> None:
    assert client.post("/orders/ORD-9999/pay").status_code == 404
