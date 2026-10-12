from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OrderItemIn(BaseModel):
    sku: str = Field(min_length=1, max_length=40)
    name: str = Field(min_length=1, max_length=200)
    quantity: int = Field(ge=1, le=100)
    unit_price_pence: int = Field(ge=0)
    unit_weight_kg: float = Field(gt=0, le=200)


class OrderCreate(BaseModel):
    customer_name: str = Field(min_length=1, max_length=200)
    phone: str = Field(min_length=3, max_length=40)
    address_line: str = Field(min_length=3, max_length=300)
    items: list[OrderItemIn] = Field(min_length=1, max_length=50)


class OrderItemOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    sku: str
    name: str
    quantity: int
    unit_price_pence: int
    unit_weight_kg: float


class OrderOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    customer_name: str
    phone: str
    address_line: str
    weight_kg: float
    total_pence: int
    status: str
    created_at: datetime
    paid_at: datetime | None
    items: list[OrderItemOut]
