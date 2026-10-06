from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import Order, OrderStatus
from app.schemas import OrderCreate, OrderOut
from app.services import orders as order_service
from app.services.orders import OrderNotFoundError, OrderNotPayableError

router = APIRouter(prefix="/orders", tags=["orders"])

SessionDep = Annotated[Session, Depends(get_session)]


def order_not_found(error: OrderNotFoundError) -> HTTPException:
    return HTTPException(404, {"code": "ORDER_NOT_FOUND", "message": str(error)})


@router.post("", status_code=201, response_model=OrderOut)
def create_order(payload: OrderCreate, session: SessionDep) -> Order:
    return order_service.create_order(session, payload)


@router.get("", response_model=list[OrderOut])
def list_orders(
    session: SessionDep, status: Annotated[OrderStatus | None, Query()] = None
) -> list[Order]:
    return order_service.list_orders(session, status)


@router.get("/{order_id}", response_model=OrderOut)
def read_order(order_id: str, session: SessionDep) -> Order:
    try:
        return order_service.get_order(session, order_id)
    except OrderNotFoundError as error:
        raise order_not_found(error) from error


@router.post("/{order_id}/pay", response_model=OrderOut)
def pay_order(order_id: str, session: SessionDep) -> Order:
    try:
        return order_service.pay_order(session, order_id)
    except OrderNotFoundError as error:
        raise order_not_found(error) from error
    except OrderNotPayableError as error:
        raise HTTPException(409, {"code": "ORDER_NOT_PAYABLE", "message": str(error)}) from error
