# Shipping rules (internal)

Rules enforced by the shipment API. Most of them are not in the public partner docs; partners usually learn them from error responses or from their account manager.

## Weight limit and pallets

- `POST /v1/shipments` accepts parcels up to **25 000 g**. Heavier parcels get `422 WEIGHT_LIMIT_EXCEEDED`.
- Anything from **25 001 g to 1 000 000 g** must go through the pallet service: `POST /v1/pallets`. The body is the same as for a shipment, but instead of `parcel` it has `pallet: { "weight_g": <int>, "pallet_type": "EUR" }`. Only `EUR` pallets exist today. Weights outside the range get `422 PALLET_WEIGHT_OUT_OF_RANGE`.
- The pallet endpoint is not in the public docs because only a few partners need it (furniture, bikes, garden equipment). Bike shops selling cargo bikes always hit it.

## Size is derived from weight

The `size` field must match the weight. Partners keep sending a size based on dimensions, which our sorting centres do not use.

| Size | Weight            |
| ---- | ----------------- |
| `A`  | up to 5 000 g     |
| `B`  | 5 001 – 15 000 g  |
| `C`  | 15 001 – 25 000 g |

A mismatch returns `422 SIZE_WEIGHT_MISMATCH`.

## Postcode must match the city

The postcode area (the leading letters, e.g. `SW` in `SW1A 1AA`) must belong to the city (`src/postcodes.js`, table of areas for the cities we serve). A mismatch returns `422 POSTCODE_CITY_MISMATCH`. Postcode areas outside the table are accepted unless the city is one of the cities in the table. City names are compared without case and surrounding spaces, but punctuation matters (`Newcastle-upon-Tyne` is not `Newcastle upon Tyne`).

This check was added after a wave of misrouted parcels caused by shops that autocomplete the city from the customer profile instead of the delivery address.

## Formats

- Phone: `^\+447\d{9}$` (UK mobile in E.164), otherwise `422 INVALID_PHONE`. No spaces, no `0044`, no national format.
- Postcode: `^[A-Z]{1,2}\d[A-Z\d]? \d[A-Z]{2}$` (uppercase, exactly one space), otherwise `422 INVALID_POSTCODE`.
- Country: only `GB`.
- Unknown fields are rejected with `400 INVALID_REQUEST`.

Order of checks: schema → phone → postcode format → weight limit → size → postcode/city.

## Pricing

| Service      | Price                |
| ------------ | -------------------- |
| Parcel `A`   | 12.99 GBP (`1299` p) |
| Parcel `B`   | 15.99 GBP (`1599` p) |
| Parcel `C`   | 19.99 GBP (`1999` p) |
| Pallet `EUR` | 89.00 GBP (`8900` p) |

Every created shipment produces one charge. See `idempotency.md` for how duplicates are charged.

## Identifiers

- Parcels: `shp_<n>`, tracking number `PCL<n>GB`.
- Pallets: `plt_<n>`, tracking number `PAL<n>GB`.

`n` is a counter per service starting at 1 (reset together with sandbox data).
