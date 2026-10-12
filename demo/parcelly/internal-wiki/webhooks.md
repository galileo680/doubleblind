# Webhooks (internal)

## Events

Only `shipment.status_changed` exists. It is sent when a shipment moves to a different status. Creating a shipment does **not** send an event, and setting the same status again does not send one either.

```json
{
  "event": "shipment.status_changed",
  "shipment_id": "shp_1",
  "reference": "ORDER-123",
  "status": "DELIVERED",
  "occurred_at": "2026-10-06T12:00:00.000Z"
}
```

## Signature

- Header: `X-Parcelly-Signature: sha256=<hex>`.
- `<hex>` is the lowercase hex HMAC-SHA256 of the **raw request body** with the partner's webhook secret as the key.
- Partners must verify the signature over the exact bytes they received, before parsing JSON. Re-serialising the parsed body breaks the signature (key order, spacing, Unicode escaping).
- The public docs only say that the header exists; partners get these details from their account manager.

## Delivery and retries

- Each partner has one endpoint (`POST /v1/webhooks` replaces it).
- A delivery counts as successful on any `2xx` response.
- Otherwise (other status, timeout after 5 s, connection error) we retry **up to 5 times** after 0.5 s, 1 s, 2 s, 4 s and 8 s. After that the event is dropped and only logged.
- Every attempt sends exactly the same body and signature.
- Events of one shipment are delivered independently, so a retried older event can arrive after a newer one. Partners should rely on `occurred_at`.

## Sandbox

In sandbox mode (`SANDBOX=1`) status changes are triggered manually:

- `POST /__sandbox/shipments/{id}/advance` with `{ "to": "COLLECTED" | "IN_TRANSIT" | "DELIVERED" | "RETURNED" }`;
- `POST /__sandbox/shipments/by-reference/{reference}/advance` with the same body (newest shipment with that reference).

Other sandbox endpoints: `POST /__sandbox/reset` (clears all data), `POST /__sandbox/partners` (`{name, api_key?, flags?}` → `{partner_id, api_key}`), `GET /__sandbox/charges?reference=...` (`{reference, count, total_pence}`).
