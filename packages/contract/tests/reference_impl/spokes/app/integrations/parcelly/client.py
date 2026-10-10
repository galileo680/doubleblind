import os
from typing import Any

import httpx

TIMEOUT_S = 10.0


class ParcellyRejectedError(Exception):
    def __init__(self, status: int, body: str) -> None:
        super().__init__(f"Parcelly rejected the shipment with {status}: {body}")


def create_shipment(path: str, payload: dict[str, Any], idempotency_key: str) -> tuple[str, str]:
    response = httpx.post(
        f"{os.environ['PARCELLY_BASE_URL'].rstrip('/')}{path}",
        json=payload,
        headers={
            "Authorization": f"Bearer {os.environ['PARCELLY_API_KEY']}",
            "Idempotency-Key": idempotency_key,
        },
        timeout=TIMEOUT_S,
    )
    if 400 <= response.status_code < 500:
        raise ParcellyRejectedError(response.status_code, response.text)
    response.raise_for_status()
    data = response.json()
    return data["id"], data["tracking_number"]
