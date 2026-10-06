# Idempotency (internal)

## Current behaviour

- With an `Idempotency-Key` header, a repeated request with the same key (per partner) returns the **same shipment** with `201` and the header `Idempotent-Replayed: true`. No second charge.
- The stored key is used even if the body differs from the first request.
- Keys of rejected requests (`4xx`) are not stored, so a corrected retry with the same key creates the shipment.
- A blank key is treated as no key. Keys longer than 255 characters get `400 INVALID_IDEMPOTENCY_KEY`.

## Legacy behaviour without a key

Requests **without** `Idempotency-Key` are accepted, because the first partners integrated before the header existed. Every such request creates a **new shipment and a new charge**, even if `reference` repeats.

This is the main source of support tickets: shops whose payment gateway retries notifications create two shipments for one order and are charged twice. Refunds are manual.

## Planned: `require_idempotency_key`

Proposal agreed with the partner team, **not implemented yet**:

- new partner flag `require_idempotency_key` (default `false`, so existing partners keep the legacy behaviour);
- when the flag is `true`, a request without the header gets `400 IDEMPOTENCY_KEY_REQUIRED` with a message telling the partner to send the header;
- the flag should be enabled for every new partner during onboarding.

Partner flags are already stored on the partner record (`flags` in `src/partners.js`), but nothing reads this one yet.
