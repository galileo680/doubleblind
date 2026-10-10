import hashlib
import hmac
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.integrations.parcelly.address import ParsedAddress, parse_address
from app.integrations.parcelly.mapping import (
    build_request,
    kilograms_to_grams,
    normalize_phone,
    size_for,
)

SECRET = "test-webhook-secret"


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ("ul. Długa 5/3, 00-001 Warszawa", ParsedAddress("Długa", "5", "3", "00-001", "Warszawa")),
        (
            "al. Jana Pawła II 12 m. 4, 00-950 Warszawa",
            ParsedAddress("al. Jana Pawła II", "12", "4", "00-950", "Warszawa"),
        ),
        ("Rynek 1, 50-101 Wrocław", ParsedAddress("Rynek", "1", None, "50-101", "Wrocław")),
        (
            "os. Tysiąclecia 7/12, 40-871 Katowice",
            ParsedAddress("os. Tysiąclecia", "7", "12", "40-871", "Katowice"),
        ),
        (
            "ul. 3 Maja 15A/2, 35-030 Rzeszów",
            ParsedAddress("3 Maja", "15A", "2", "35-030", "Rzeszów"),
        ),
    ],
)
def test_parses_addresses(line: str, expected: ParsedAddress) -> None:
    assert parse_address(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "Kowalska bez numeru, Warszawa",
        "ul. Marszałkowska 84/92 lok. 5, 00-514 Warszawa",
        "",
    ],
)
def test_refuses_addresses_it_cannot_split_safely(line: str) -> None:
    assert parse_address(line) is None


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        ("600 100 200", "+48600100200"),
        ("+48600100200", "+48600100200"),
        ("0048 600-100-200", "+48600100200"),
        ("(+48) 602-345-678", "+48602345678"),
        ("48604567890", "+48604567890"),
        ("12345", None),
    ],
)
def test_normalizes_phones(phone: str, expected: str | None) -> None:
    assert normalize_phone(phone) == expected


def test_converts_kilograms_to_whole_grams_rounding_up() -> None:
    assert kilograms_to_grams(14.5) == 14500
    assert kilograms_to_grams(0.3) == 300
    assert kilograms_to_grams(0.0001) == 1
    assert kilograms_to_grams(1.0004) == 1001


def test_size_follows_the_weight() -> None:
    assert [size_for(grams) for grams in (5000, 5001, 15000, 15001, 25000)] == [
        "A",
        "B",
        "B",
        "C",
        "C",
    ]


def order(**overrides: Any) -> Any:
    values = {"id": "ORD-1001", "customer_name": "Jan Kowalski", "weight_kg": 14.5}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_light_orders_become_parcels_and_heavy_ones_pallets() -> None:
    address = ParsedAddress("Długa", "5", "3", "00-001", "Warszawa")

    parcel_path, parcel = build_request(order(), address, "+48600100200")
    pallet_path, pallet = build_request(order(weight_kg=27.0), address, "+48600100200")

    assert parcel_path == "/v1/shipments"
    assert parcel["parcel"] == {"weight_g": 14500, "size": "B"}
    assert parcel["receiver"]["address"]["country"] == "PL"
    assert pallet_path == "/v1/pallets"
    assert pallet["pallet"] == {"weight_g": 27000, "pallet_type": "EUR"}


def signed(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_rejects_a_bad_signature(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PARCELLY_WEBHOOK_SECRET", SECRET)
    body = b'{"event":"shipment.status_changed","reference":"ORD-1","status":"DELIVERED"}'

    response = client.post(
        "/integrations/parcelly/webhook",
        content=body,
        headers={"X-Parcelly-Signature": "sha256=" + "0" * 64},
    )

    assert response.status_code == 401


def test_webhook_updates_the_order_status(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, paid_handlers: list[Any]
) -> None:
    monkeypatch.setenv("PARCELLY_WEBHOOK_SECRET", SECRET)
    created = client.post(
        "/orders",
        json={
            "customer_name": "Jan Kowalski",
            "phone": "600 100 200",
            "address_line": "ul. Długa 5/3, 00-001 Warszawa",
            "items": [
                {
                    "sku": "BELL",
                    "name": "Brass bell",
                    "quantity": 1,
                    "unit_price_gr": 3900,
                    "unit_weight_kg": 0.2,
                }
            ],
        },
    ).json()
    body = (
        '{"event":"shipment.status_changed","shipment_id":"shp_9","reference":"'
        + created["id"]
        + '","status":"RETURNED","occurred_at":"2026-10-06T12:00:00Z"}'
    ).encode()

    response = client.post(
        "/integrations/parcelly/webhook",
        content=body,
        headers={"X-Parcelly-Signature": signed(body)},
    )

    assert response.status_code == 200
    assert client.get(f"/orders/{created['id']}").json()["status"] == "returned"
