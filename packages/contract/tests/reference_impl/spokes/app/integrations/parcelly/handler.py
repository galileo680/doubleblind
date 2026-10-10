import logging

import httpx

from app.db import SessionLocal
from app.integrations.parcelly.address import parse_address
from app.integrations.parcelly.client import (
    CreatedShipment,
    ParcellyNotConfiguredError,
    ParcellyRejectedError,
    create_shipment,
)
from app.integrations.parcelly.mapping import build_request, normalize_phone
from app.models import Order, OrderStatus
from app.services.order_events import ORDER_PAID_HANDLERS

logger = logging.getLogger(__name__)


def mark_for_review(order_id: str, reason: str) -> None:
    logger.warning("order %s needs review: %s", order_id, reason)
    with SessionLocal() as session:
        order = session.get(Order, order_id)
        if order is not None:
            order.status = OrderStatus.NEEDS_REVIEW.value
            session.commit()


def save_shipment(order_id: str, shipment: CreatedShipment) -> None:
    with SessionLocal() as session:
        order = session.get(Order, order_id)
        if order is not None:
            order.shipment_id = shipment.id
            order.tracking_number = shipment.tracking_number
            session.commit()


def ship_paid_order(order: Order) -> None:
    if order.shipment_id:
        return
    address = parse_address(order.address_line)
    if address is None:
        mark_for_review(order.id, f"address cannot be split: {order.address_line!r}")
        return
    phone = normalize_phone(order.phone)
    if phone is None:
        mark_for_review(order.id, f"phone cannot be normalized: {order.phone!r}")
        return
    path, payload = build_request(order, address, phone)
    try:
        shipment = create_shipment(path, payload, idempotency_key=order.id)
    except ParcellyNotConfiguredError as error:
        logger.warning("Parcelly integration is not configured: %s", error)
        return
    except ParcellyRejectedError as error:
        mark_for_review(order.id, str(error))
        return
    except httpx.HTTPError as error:
        logger.warning("Parcelly is unavailable, order %s stays paid: %s", order.id, error)
        return
    save_shipment(order.id, shipment)


def register_handler() -> None:
    if ship_paid_order not in ORDER_PAID_HANDLERS:
        ORDER_PAID_HANDLERS.append(ship_paid_order)
