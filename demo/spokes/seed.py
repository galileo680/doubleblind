from dataclasses import dataclass

from app.db import SessionLocal, reset_db
from app.models import OrderStatus, utcnow
from app.schemas import OrderCreate, OrderItemIn
from app.services.orders import create_order


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price_pence: int
    weight_kg: float


@dataclass(frozen=True)
class SeedOrder:
    customer_name: str
    phone: str
    address_line: str
    skus: tuple[str, ...]
    status: OrderStatus


CATALOG = {
    product.sku: product
    for product in (
        Product("CITY-U3", "Spokes Urban 3 city bike", 289900, 14.5),
        Product("CARGO-H2", "Spokes Haul 2 cargo bike", 649900, 27.0),
        Product("GRAVEL-G2", "Spokes G2 gravel bike", 459900, 9.9),
        Product("TOUR-T5", "Spokes Tour 5 trekking bike", 379900, 16.2),
        Product("SEAT-KID", "Rear child seat", 44900, 3.2),
        Product("LOCK-U", "U-lock", 22900, 1.8),
        Product("LIGHTS-SET", "Front and rear light set", 12900, 0.3),
        Product("BELL", "Brass bell", 3900, 0.2),
        Product("PANNIERS", "Pannier bags (pair)", 34900, 2.1),
        Product("PUMP", "Floor pump", 15900, 1.4),
        Product("RACK-CARGO", "Cargo front rack", 59900, 4.5),
    )
}

PAID_STATUSES = frozenset({OrderStatus.PAID, OrderStatus.SHIPPED, OrderStatus.DELIVERED})

SEED_ORDERS = (
    SeedOrder(
        "John Smith",
        "07700 900200",
        "Flat 3, 5 Long Street, London E1 6AN",
        ("CITY-U3",),
        OrderStatus.PENDING,
    ),
    SeedOrder(
        "Emma Brown",
        "+447700900300",
        "1 Corn Street, Bristol BS1 1HQ",
        ("CARGO-H2",),
        OrderStatus.PAID,
    ),
    SeedOrder(
        "Oliver Taylor",
        "0044 7700-900200",
        "12 St John's Wood Road, Apt 4, London NW8 8RX",
        ("GRAVEL-G2",),
        OrderStatus.PAID,
    ),
    SeedOrder(
        "Sophie Wilson",
        "07700900200",
        "Flat 12, 7 Millennium Court, Leeds LS6 2AA",
        ("SEAT-KID",),
        OrderStatus.PENDING,
    ),
    SeedOrder(
        "James Davies",
        "+44 7700 900234",
        "10 Deansgate, Manchester M3 2BW",
        ("LIGHTS-SET",),
        OrderStatus.SHIPPED,
    ),
    SeedOrder(
        "Charlotte Evans",
        "(+44) 7700-900345",
        "Flat 1, 22 Broad Street, Birmingham B1 2HF",
        ("SEAT-KID", "LOCK-U"),
        OrderStatus.DELIVERED,
    ),
    SeedOrder(
        "Harry Thomas",
        "07700 900456",
        "Flat 2, 15A 3rd Avenue, Brighton BN3 2PD",
        ("CARGO-H2", "RACK-CARGO"),
        OrderStatus.PENDING,
    ),
    SeedOrder(
        "Amelia Roberts",
        "447700900567",
        "5 Bold Street, Liverpool L1 4DJ",
        ("BELL", "LIGHTS-SET"),
        OrderStatus.CANCELLED,
    ),
    SeedOrder(
        "George Walker",
        "07700 900200",
        "Behind the old mill, London",
        ("SEAT-KID", "LOCK-U"),
        OrderStatus.PAID,
    ),
    SeedOrder(
        "Isla Wright",
        "07700 900567",
        "104 Princes Street, Apt 12, Edinburgh EH2 3AA",
        ("TOUR-T5", "SEAT-KID", "PANNIERS", "PUMP", "LOCK-U", "LIGHTS-SET"),
        OrderStatus.PAID,
    ),
    SeedOrder(
        "Jack Hughes",
        "+447700900678",
        "Flat 5, 10 Park Row, Nottingham NG1 6GR",
        ("CITY-U3", "LIGHTS-SET", "BELL"),
        OrderStatus.PENDING,
    ),
    SeedOrder(
        "Grace Green",
        "+44 7700 900789",
        "Unit 5, 84-92 Oxford Street, London W1D 1BS",
        ("PANNIERS",),
        OrderStatus.DELIVERED,
    ),
    SeedOrder(
        "William Clarke",
        "07700-900890",
        "2 Grey Street, Newcastle upon Tyne NE1 6AE",
        ("GRAVEL-G2", "PUMP"),
        OrderStatus.SHIPPED,
    ),
)


def order_request(seed_order: SeedOrder) -> OrderCreate:
    items = [
        OrderItemIn(
            sku=product.sku,
            name=product.name,
            quantity=1,
            unit_price_pence=product.price_pence,
            unit_weight_kg=product.weight_kg,
        )
        for product in (CATALOG[sku] for sku in seed_order.skus)
    ]
    return OrderCreate(
        customer_name=seed_order.customer_name,
        phone=seed_order.phone,
        address_line=seed_order.address_line,
        items=items,
    )


def seed() -> int:
    reset_db()
    with SessionLocal() as session:
        for seed_order in SEED_ORDERS:
            order = create_order(session, order_request(seed_order))
            order.status = seed_order.status.value
            if seed_order.status in PAID_STATUSES:
                order.paid_at = utcnow()
            session.commit()
    return len(SEED_ORDERS)


def main() -> None:
    print(f"Seeded {seed()} orders")


if __name__ == "__main__":
    main()
