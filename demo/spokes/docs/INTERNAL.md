# Spokes order service (internal)

Spokes is an online bike shop. This service stores orders and marks them as paid when the payment gateway confirms the payment. There is **no carrier integration yet**: today the warehouse team prints shipping labels by hand from the list of paid orders.

## Running

```
pip install -r requirements.txt
uvicorn app.main:app --port 8000
python seed.py
pytest
```

The database is SQLite. `SPOKES_DATABASE_URL` overrides the default `sqlite:///./spokes.db`. Tables are created on startup; `seed.py` drops and recreates them with 13 sample orders.

## Data model

### `Order`

| Field | Type | Notes |
|---|---|---|
| `id` | `str` | `ORD-<number>`, numbers start at 1001. |
| `number` | `int` | Sequential number behind `id`. |
| `customer_name` | `str` | As typed by the customer. |
| `phone` | `str` | As typed by the customer, see below. |
| `address_line` | `str` | Whole delivery address in one field, see below. |
| `weight_kg` | `float` | Sum of `unit_weight_kg × quantity` of all items, rounded to 3 decimals. |
| `total_pence` | `int` | Order value in pence (GBP). |
| `status` | `str` | `pending`, `paid`, `shipped`, `delivered`, `cancelled`. |
| `created_at`, `paid_at` | `datetime` | UTC, stored without time zone. |
| `items` | `list[OrderItem]` | `sku`, `name`, `quantity`, `unit_price_pence`, `unit_weight_kg`. |

### Status lifecycle

```
pending ──pay──▶ paid ──(warehouse)──▶ shipped ──(warehouse)──▶ delivered
   └──(support)──▶ cancelled
```

`shipped` and `delivered` are set manually by the warehouse team today. There is no status for parcels that come back to us; support marks them `cancelled` and adds a note in the helpdesk.

## API

| Method | Path | Description |
|---|---|---|
| `POST` | `/orders` | Create an order from `customer_name`, `phone`, `address_line`, `items[]`. Returns `201`. |
| `GET` | `/orders` | List orders, optional `?status=paid`. |
| `GET` | `/orders/{id}` | One order, `404 ORDER_NOT_FOUND` if missing. |
| `POST` | `/orders/{id}/pay` | Called by the payment gateway. Allowed for `pending` and `paid` orders, otherwise `409 ORDER_NOT_PAYABLE`. |
| `GET` | `/health` | Health check. |

## Extension point: `ORDER_PAID_HANDLERS`

`app/services/order_events.py` holds the list `ORDER_PAID_HANDLERS: list[Callable[[Order], None]]`. It is empty today. Anything that should happen after a payment (shipping, invoices, e-mails) is meant to be added as a handler there.

How handlers are called by `POST /orders/{id}/pay`:

1. The order moves from `pending` to `paid` and the change is committed.
2. Every handler is called with the `Order` in the list order.
3. An exception in a handler is logged and **does not undo the payment**; the remaining handlers still run.
4. The order is reloaded from the database before the response, so changes made by a handler (in its own session from `app.db.SessionLocal`) are returned to the caller.

**The payment gateway sometimes sends the same payment confirmation twice** (retries after timeouts). The second call finds the order already `paid`, keeps the original `paid_at` and **calls the handlers again**. Handlers must therefore be idempotent: running twice for the same order must not, for example, create two shipments.

## Customer data quirks

### Addresses

Customers type the whole address into one free-text field and we never normalised it. Typical forms:

- `Flat 3, 5 Long Street, London E1 6AN` (flat 3, building 5)
- `12 St John's Wood Road, Apt 4, London NW8 8RX` (multi-word street name, flat written after the street as `Apt 4`)
- `1 Corn Street, Bristol BS1 1HQ` (no flat)
- `Flat 12, 7 Millennium Court, Leeds LS6 2AA` (block of flats)
- `Unit 5, 84-92 Oxford Street, London W1D 1BS` (building number `84-92` is one building; the flat is `Unit 5`)
- `Flat 2, 15A 3rd Avenue, Brighton BN3 2PD` (street name starting with a number, building with a letter)

The postcode always comes last, after the city, in the UK format with one space (`SW1A 1AA`).

Some addresses are incomplete, e.g. `Behind the old mill, London` (no building number, no postcode). The warehouse calls the customer in such cases before shipping.

### Phone numbers

Typed in any format: `07700 900200`, `07700900200`, `+447700900200`, `+44 7700 900234`, `0044 7700-900200`, `(+44) 7700-900345`, `447700900567`, `07700-900890`. All customers are in the United Kingdom.

### Weight

Weights are in **kilograms as floats** (`0.3`, `14.5`, `27.0`). Accessories can weigh a few hundred grams. **Cargo bikes weigh more than 25 kg** (`Spokes Haul 2` is 27 kg, 31.5 kg with the front rack), so they never fit standard courier parcels.

## Tests

`tests/test_orders.py` covers creating, reading, listing and paying orders, including duplicate payment confirmations and failing handlers. The `paid_handlers` fixture swaps the registered handlers for test handlers during a test.
