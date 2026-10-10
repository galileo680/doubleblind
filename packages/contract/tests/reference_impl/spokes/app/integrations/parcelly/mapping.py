import math
import re
from typing import Any

from app.integrations.parcelly.address import ParsedAddress
from app.models import Order

PALLET_THRESHOLD_G = 25000
SIZE_LIMITS_G = (("A", 5000), ("B", 15000), ("C", 25000))
SHIPMENTS_PATH = "/v1/shipments"
PALLETS_PATH = "/v1/pallets"


def kilograms_to_grams(weight_kg: float) -> int:
    return max(1, math.ceil(round(weight_kg * 1000, 6)))


def size_for(weight_g: int) -> str:
    return next(size for size, limit in SIZE_LIMITS_G if weight_g <= limit)


def normalize_phone(phone: str) -> str | None:
    digits = re.sub(r"\D", "", phone)
    if len(digits) == 9:
        return f"+48{digits}"
    if len(digits) == 11 and digits.startswith("48"):
        return f"+{digits}"
    if len(digits) == 13 and digits.startswith("0048"):
        return f"+{digits[2:]}"
    return None


def build_request(order: Order, address: ParsedAddress, phone: str) -> tuple[str, dict[str, Any]]:
    weight_g = kilograms_to_grams(order.weight_kg)
    receiver = {"name": order.customer_name, "phone": phone, "address": address.to_payload()}
    if weight_g > PALLET_THRESHOLD_G:
        pallet = {"weight_g": weight_g, "pallet_type": "EUR"}
        return PALLETS_PATH, {"reference": order.id, "receiver": receiver, "pallet": pallet}
    parcel = {"weight_g": weight_g, "size": size_for(weight_g)}
    return SHIPMENTS_PATH, {"reference": order.id, "receiver": receiver, "parcel": parcel}
