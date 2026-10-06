from dataclasses import dataclass

from app.db import SessionLocal, reset_db
from app.models import OrderStatus, utcnow
from app.schemas import OrderCreate, OrderItemIn
from app.services.orders import create_order


@dataclass(frozen=True)
class Product:
    sku: str
    name: str
    price_gr: int
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
        "Jan Kowalski",
        "600 100 200",
        "ul. Długa 5/3, 00-001 Warszawa",
        ("CITY-U3",),
        OrderStatus.PENDING,
    ),
    SeedOrder(
        "Anna Nowak",
        "+48500200300",
        "Rynek 1, 50-101 Wrocław",
        ("CARGO-H2",),
        OrderStatus.PAID,
    ),
    SeedOrder(
        "Piotr Wiśniewski",
        "0048 600-100-200",
        "al. Jana Pawła II 12 m. 4, 00-950 Warszawa",
        ("GRAVEL-G2",),
        OrderStatus.PAID,
    ),
    SeedOrder(
        "Katarzyna Wójcik",
        "600100200",
        "os. Tysiąclecia 7/12, 40-871 Katowice",
        ("SEAT-KID",),
        OrderStatus.PENDING,
    ),
    SeedOrder(
        "Tomasz Kamiński",
        "+48 601 234 567",
        "ul. Floriańska 3, 31-019 Kraków",
        ("LIGHTS-SET",),
        OrderStatus.SHIPPED,
    ),
    SeedOrder(
        "Magdalena Lewandowska",
        "(+48) 602-345-678",
        "Plac Wolności 9, 61-738 Poznań",
        ("SEAT-KID", "LOCK-U"),
        OrderStatus.DELIVERED,
    ),
    SeedOrder(
        "Michał Zieliński",
        "603 456 789",
        "ul. 3 Maja 15A/2, 35-030 Rzeszów",
        ("CARGO-H2", "RACK-CARGO"),
        OrderStatus.PENDING,
    ),
    SeedOrder(
        "Agnieszka Szymańska",
        "48604567890",
        "ul. Świętego Ducha 2, 80-834 Gdańsk",
        ("BELL", "LIGHTS-SET"),
        OrderStatus.CANCELLED,
    ),
    SeedOrder(
        "Krzysztof Woźniak",
        "600 100 200",
        "Kowalska bez numeru, Warszawa",
        ("SEAT-KID", "LOCK-U"),
        OrderStatus.PAID,
    ),
    SeedOrder(
        "Ewa Dąbrowska",
        "604 567 890",
        "ul. Piotrkowska 104 m. 12, 90-004 Łódź",
        ("TOUR-T5", "SEAT-KID", "PANNIERS", "PUMP", "LOCK-U", "LIGHTS-SET"),
        OrderStatus.PAID,
    ),
    SeedOrder(
        "Paweł Kozłowski",
        "+48605678901",
        "ul. Mickiewicza 10/5, 15-213 Białystok",
        ("CITY-U3", "LIGHTS-SET", "BELL"),
        OrderStatus.PENDING,
    ),
    SeedOrder(
        "Joanna Jankowska",
        "+48 606 789 012",
        "ul. Marszałkowska 84/92 lok. 5, 00-514 Warszawa",
        ("PANNIERS",),
        OrderStatus.DELIVERED,
    ),
    SeedOrder(
        "Marcin Mazur",
        "607-890-123",
        "al. Grunwaldzka 141, 80-264 Gdańsk",
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
            unit_price_gr=product.price_gr,
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
