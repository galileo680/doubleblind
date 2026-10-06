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

The first two digits of the postcode must belong to the city (`src/postcodes.js`, table of prefixes for the cities we serve). A mismatch returns `422 POSTCODE_CITY_MISMATCH`. Postcodes outside the table are accepted unless the city is one of the cities in the table. City names are compared without case and surrounding spaces, but Polish characters matter (`Wroclaw` is not `Wrocław`).

This check was added after a wave of misrouted parcels caused by shops that autocomplete the city from the customer profile instead of the delivery address.

## Formats

- Phone: `^\+48\d{9}$`, otherwise `422 INVALID_PHONE`. No spaces, no `0048`, no national format.
- Postcode: `^\d{2}-\d{3}$`, otherwise `422 INVALID_POSTCODE`.
- Country: only `PL`.
- Unknown fields are rejected with `400 INVALID_REQUEST`.

Order of checks: schema → phone → postcode format → weight limit → size → postcode/city.

## Pricing

| Service      | Price                 |
| ------------ | --------------------- |
| Parcel `A`   | 12.99 PLN (`1299` gr) |
| Parcel `B`   | 15.99 PLN (`1599` gr) |
| Parcel `C`   | 19.99 PLN (`1999` gr) |
| Pallet `EUR` | 89.00 PLN (`8900` gr) |

Every created shipment produces one charge. See `idempotency.md` for how duplicates are charged.

## Identifiers

- Parcels: `shp_<n>`, tracking number `PCL<n>PL`.
- Pallets: `plt_<n>`, tracking number `PAL<n>PL`.

`n` is a counter per service starting at 1 (reset together with sandbox data).
