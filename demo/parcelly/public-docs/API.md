# Parcelly Partner API

Parcelly delivers parcels across the United Kingdom. This document describes the API available to partner shops.

## Authentication

Every request to `/v1/*` needs a partner API key issued by your Parcelly account manager:

```
Authorization: Bearer <api_key>
```

A missing or unknown key returns `401 UNAUTHORIZED`.

## Create a shipment

`POST /v1/shipments`

```json
{
  "reference": "ORDER-123",
  "receiver": {
    "name": "John Smith",
    "phone": "+447700900200",
    "address": {
      "street": "Long Street",
      "building": "5",
      "flat": "3",
      "postcode": "E1 6AN",
      "city": "London",
      "country": "GB"
    }
  },
  "parcel": { "weight_g": 1200, "size": "A" }
}
```

| Field              | Description                                                                                                    |
| ------------------ | -------------------------------------------------------------------------------------------------------------- |
| `reference`        | Your own identifier of the order, up to 64 characters.                                                         |
| `receiver.name`    | Receiver name.                                                                                                 |
| `receiver.phone`   | Mobile number in the format `+447XXXXXXXXX`.                                                                   |
| `receiver.address` | `street`, `building`, optional `flat`, `postcode` (UK format, e.g. `SW1A 1AA`), `city`, `country` (only `GB`). |
| `parcel.weight_g`  | Weight in grams (integer).                                                                                     |
| `parcel.size`      | Parcel size: `A`, `B` or `C`.                                                                                  |

Response `201 Created`:

```json
{
  "id": "shp_1",
  "service": "parcel",
  "reference": "ORDER-123",
  "status": "CREATED",
  "tracking_number": "PCL1GB",
  "price_pence": 1299,
  "receiver": { "...": "..." },
  "parcel": { "weight_g": 1200, "size": "A" },
  "created_at": "2026-10-06T10:00:00.000Z",
  "updated_at": "2026-10-06T10:00:00.000Z"
}
```

## Read shipments

- `GET /v1/shipments/{id}` returns one shipment.
- `GET /v1/shipments?reference=ORDER-123` returns the most recent shipment created with this reference.

Unknown shipments return `404 SHIPMENT_NOT_FOUND`.

## Statuses

`CREATED` → `COLLECTED` → `IN_TRANSIT` → `DELIVERED`. A parcel that cannot be delivered ends as `RETURNED`.

## Webhooks

`POST /v1/webhooks`

```json
{ "url": "https://shop.example.com/parcelly/webhook", "secret": "<your secret>" }
```

Each partner has one webhook endpoint; registering again replaces it. Parcelly sends `POST` requests with the event below whenever a shipment changes status:

```json
{
  "event": "shipment.status_changed",
  "shipment_id": "shp_1",
  "reference": "ORDER-123",
  "status": "DELIVERED",
  "occurred_at": "2026-10-06T12:00:00.000Z"
}
```

Webhook requests carry the `X-Parcelly-Signature` header. Ask your account manager how to verify it.

## Errors

Errors use one format:

```json
{ "code": "INVALID_PHONE", "message": "Receiver phone must have the format +447XXXXXXXXX" }
```

| Status | Code                 | Meaning                                        |
| ------ | -------------------- | ---------------------------------------------- |
| 400    | `INVALID_REQUEST`    | The body does not match the documented fields. |
| 400    | `INVALID_JSON`       | The body is not valid JSON.                    |
| 401    | `UNAUTHORIZED`       | Missing or unknown API key.                    |
| 404    | `SHIPMENT_NOT_FOUND` | No such shipment.                              |
| 422    | `INVALID_PHONE`      | Phone is not in the `+447XXXXXXXXX` format.    |
| 422    | `INVALID_POSTCODE`   | Postcode is not a valid UK postcode.           |

## Test environment

The partner sandbox resets every night. Ask your account manager for sandbox credentials.
