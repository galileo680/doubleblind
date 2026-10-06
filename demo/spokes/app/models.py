from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class OrderStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    SHIPPED = "shipped"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[str] = mapped_column(String(20), primary_key=True)
    number: Mapped[int] = mapped_column(unique=True)
    customer_name: Mapped[str] = mapped_column(String(200))
    phone: Mapped[str] = mapped_column(String(40))
    address_line: Mapped[str] = mapped_column(String(300))
    weight_kg: Mapped[float]
    total_gr: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default=OrderStatus.PENDING.value)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    paid_at: Mapped[datetime | None] = mapped_column(default=None)
    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="OrderItem.id",
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[str] = mapped_column(ForeignKey("orders.id"))
    sku: Mapped[str] = mapped_column(String(40))
    name: Mapped[str] = mapped_column(String(200))
    quantity: Mapped[int]
    unit_price_gr: Mapped[int]
    unit_weight_kg: Mapped[float]
    order: Mapped[Order] = relationship(back_populates="items")
