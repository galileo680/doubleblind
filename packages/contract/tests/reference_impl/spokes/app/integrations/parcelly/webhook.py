import hashlib
import hmac
import json
import os

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.db import SessionLocal
from app.models import Order, OrderStatus

SIGNATURE_HEADER = "X-Parcelly-Signature"
SECRET_ENV = "PARCELLY_WEBHOOK_SECRET"
STATUS_MAPPING = {
    "CREATED": OrderStatus.PAID,
    "COLLECTED": OrderStatus.SHIPPED,
    "IN_TRANSIT": OrderStatus.SHIPPED,
    "DELIVERED": OrderStatus.DELIVERED,
    "RETURNED": OrderStatus.RETURNED,
}

router = APIRouter(prefix="/integrations/parcelly", tags=["parcelly"])


def signature_is_valid(raw_body: bytes, header_value: str | None) -> bool:
    secret = os.environ.get(SECRET_ENV)
    if not secret or not header_value:
        return False
    expected = "sha256=" + hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header_value, expected)


@router.post("/webhook")
async def receive_status_change(request: Request) -> JSONResponse:
    raw_body = await request.body()
    if not signature_is_valid(raw_body, request.headers.get(SIGNATURE_HEADER)):
        return JSONResponse(
            status_code=401, content={"code": "INVALID_SIGNATURE", "message": "bad signature"}
        )
    try:
        event = json.loads(raw_body)
    except ValueError:
        return JSONResponse(
            status_code=400, content={"code": "INVALID_JSON", "message": "bad body"}
        )
    new_status = STATUS_MAPPING.get(event.get("status"))
    reference = event.get("reference")
    if new_status is None or not isinstance(reference, str):
        return JSONResponse(status_code=200, content={"status": "ignored"})
    with SessionLocal() as session:
        order = session.get(Order, reference)
        if order is None:
            return JSONResponse(status_code=200, content={"status": "ignored"})
        order.status = new_status.value
        if order.shipment_id is None and isinstance(event.get("shipment_id"), str):
            order.shipment_id = event["shipment_id"]
        session.commit()
    return JSONResponse(status_code=200, content={"status": "accepted"})
