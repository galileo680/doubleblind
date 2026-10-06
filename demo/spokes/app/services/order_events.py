import logging
from collections.abc import Callable

from app.models import Order

logger = logging.getLogger(__name__)

OrderPaidHandler = Callable[[Order], None]

ORDER_PAID_HANDLERS: list[OrderPaidHandler] = []


def emit_order_paid(order: Order) -> None:
    for handler in ORDER_PAID_HANDLERS:
        try:
            handler(order)
        except Exception:
            name = getattr(handler, "__name__", repr(handler))
            logger.exception("order_paid handler %s failed for order %s", name, order.id)
