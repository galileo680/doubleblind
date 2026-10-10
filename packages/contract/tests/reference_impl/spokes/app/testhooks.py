from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db import get_session, reset_db
from app.models import Order, OrderStatus, utcnow
from app.services.orders import next_order_number

router = APIRouter(prefix="/__doubleblind", tags=["test hooks"])

SessionDep = Annotated[Session, Depends(get_session)]


class HookOrderIn(BaseModel):
    customer_name: str = Field(min_length=1)
    phone: str = Field(min_length=1)
    address_line: str = Field(min_length=1)
    weight_kg: float = Field(gt=0)
    status: str = OrderStatus.PENDING.value
    shipment_id: str | None = None


@router.post("/reset")
def reset() -> dict[str, str]:
    reset_db()
    return {"status": "reset"}


@router.post("/orders", status_code=201)
def create_order(payload: HookOrderIn, session: SessionDep) -> dict[str, str]:
    number = next_order_number(session)
    order = Order(
        id=f"ORD-{number}",
        number=number,
        customer_name=payload.customer_name,
        phone=payload.phone,
        address_line=payload.address_line,
        weight_kg=payload.weight_kg,
        total_gr=0,
        status=payload.status,
        shipment_id=payload.shipment_id,
        paid_at=None if payload.status == OrderStatus.PENDING.value else utcnow(),
        items=[],
    )
    session.add(order)
    session.commit()
    return {"id": order.id}
