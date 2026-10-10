from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Order, OrderItem, OrderStatus, utcnow
from app.schemas import OrderCreate
from app.services.order_events import emit_order_paid

FIRST_ORDER_NUMBER = 1001
PAYABLE_STATUSES = frozenset({OrderStatus.PENDING, OrderStatus.PAID})


class OrderNotFoundError(LookupError):
    def __init__(self, order_id: str) -> None:
        super().__init__(f"Order {order_id} not found")


class OrderNotPayableError(ValueError):
    def __init__(self, order: Order) -> None:
        super().__init__(f"Order {order.id} cannot be paid in status '{order.status}'")


def next_order_number(session: Session) -> int:
    current = session.scalar(select(func.max(Order.number)))
    return FIRST_ORDER_NUMBER if current is None else current + 1


def create_order(session: Session, payload: OrderCreate) -> Order:
    number = next_order_number(session)
    order = Order(
        id=f"ORD-{number}",
        number=number,
        customer_name=payload.customer_name.strip(),
        phone=payload.phone.strip(),
        address_line=payload.address_line.strip(),
        weight_kg=round(sum(item.unit_weight_kg * item.quantity for item in payload.items), 3),
        total_gr=sum(item.unit_price_gr * item.quantity for item in payload.items),
        status=OrderStatus.PENDING.value,
        items=[OrderItem(**item.model_dump()) for item in payload.items],
    )
    session.add(order)
    session.commit()
    return order


def get_order(session: Session, order_id: str) -> Order:
    order = session.get(Order, order_id)
    if order is None:
        raise OrderNotFoundError(order_id)
    return order


def list_orders(session: Session, status: OrderStatus | None = None) -> list[Order]:
    query = select(Order).order_by(Order.number)
    if status is not None:
        query = query.where(Order.status == status.value)
    return list(session.scalars(query))


def pay_order(session: Session, order_id: str) -> Order:
    order = get_order(session, order_id)
    if order.status not in PAYABLE_STATUSES:
        raise OrderNotPayableError(order)
    if order.status == OrderStatus.PENDING:
        order.status = OrderStatus.PAID.value
        order.paid_at = utcnow()
        session.commit()
    emit_order_paid(order)
    session.refresh(order)
    return order
