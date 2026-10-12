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
        (
            "Flat 3, 5 Long Street, London E1 6AN",
            ParsedAddress("Long Street", "5", "3", "E1 6AN", "London"),
        ),
        (
            "12 St John's Wood Road, Apt 4, London NW8 8RX",
            ParsedAddress("St John's Wood Road", "12", "4", "NW8 8RX", "London"),
        ),
        (
            "1 Corn Street, Bristol BS1 1HQ",
            ParsedAddress("Corn Street", "1", None, "BS1 1HQ", "Bristol"),
        ),
        (
            "Flat 12, 7 Millennium Court, Leeds LS6 2AA",
            ParsedAddress("Millennium Court", "7", "12", "LS6 2AA", "Leeds"),
        ),
        (
            "Unit 5, 84-92 Oxford Street, London W1D 1BS",
            ParsedAddress("Oxford Street", "84-92", "5", "W1D 1BS", "London"),
        ),
        (
            "Flat 2, 15A 3rd Avenue, Brighton BN3 2PD",
            ParsedAddress("3rd Avenue", "15A", "2", "BN3 2PD", "Brighton"),
        ),
    ],
)
def test_parses_addresses(line: str, expected: ParsedAddress) -> None:
    assert parse_address(line) == expected


@pytest.mark.parametrize(
    "line",
    [
        "Behind the old mill, London",
        "Flat 3, Long Street, London E1 6AN",
        "",
    ],
)
def test_refuses_addresses_it_cannot_split_safely(line: str) -> None:
    assert parse_address(line) is None


@pytest.mark.parametrize(
    ("phone", "expected"),
    [
        ("07700 900200", "+447700900200"),
        ("+447700900200", "+447700900200"),
        ("0044 7700-900200", "+447700900200"),
        ("(+44) 7700-900345", "+447700900345"),
        ("447700900567", "+447700900567"),
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
    values = {"id": "ORD-1001", "customer_name": "John Smith", "weight_kg": 14.5}
    values.update(overrides)
    return SimpleNamespace(**values)


def test_light_orders_become_parcels_and_heavy_ones_pallets() -> None:
    address = ParsedAddress("Long Street", "5", "3", "E1 6AN", "London")

    parcel_path, parcel = build_request(order(), address, "+447700900200")
    pallet_path, pallet = build_request(order(weight_kg=27.0), address, "+447700900200")

    assert parcel_path == "/v1/shipments"
    assert parcel["parcel"] == {"weight_g": 14500, "size": "B"}
    assert parcel["receiver"]["address"]["country"] == "GB"
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
            "customer_name": "John Smith",
            "phone": "07700 900200",
            "address_line": "Flat 3, 5 Long Street, London E1 6AN",
            "items": [
                {
                    "sku": "BELL",
                    "name": "Brass bell",
                    "quantity": 1,
                    "unit_price_pence": 3900,
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
