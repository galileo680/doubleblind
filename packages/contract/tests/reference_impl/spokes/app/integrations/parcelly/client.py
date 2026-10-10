import os
from dataclasses import dataclass
from typing import Any

import httpx

BASE_URL_ENV = "PARCELLY_BASE_URL"
API_KEY_ENV = "PARCELLY_API_KEY"
TIMEOUT_S = 10.0


class ParcellyNotConfiguredError(Exception):
    pass


class ParcellyRejectedError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Parcelly rejected the shipment with {status}: {body}")
        self.status = status


@dataclass(frozen=True)
class CreatedShipment:
    id: str
    tracking_number: str


def create_shipment(path: str, payload: dict[str, Any], idempotency_key: str) -> CreatedShipment:
    base_url = os.environ.get(BASE_URL_ENV)
    api_key = os.environ.get(API_KEY_ENV)
    if not base_url or not api_key:
        raise ParcellyNotConfiguredError(f"set {BASE_URL_ENV} and {API_KEY_ENV}")
    response = httpx.post(
        f"{base_url.rstrip('/')}{path}",
        json=payload,
        headers={"Authorization": f"Bearer {api_key}", "Idempotency-Key": idempotency_key},
        timeout=TIMEOUT_S,
    )
    if 400 <= response.status_code < 500:
        raise ParcellyRejectedError(response.status_code, response.text)
    response.raise_for_status()
    data = response.json()
    return CreatedShipment(id=data["id"], tracking_number=data["tracking_number"])
