import argparse
import asyncio
import os
import sys

from demo_workspace import REPO_ROOT

from doubleblind_agent.egress_guard import classify
from doubleblind_contract.guard_rules import SyntheticAllowlist, check_message
from doubleblind_contract.loader import parse_yaml
from doubleblind_llm import LLMClient

CASSETTE = REPO_ROOT / "demo" / "recordings" / "egress.jsonl"
REPO_PATHS = (
    "src/shipments.js",
    "src/webhooks.js",
    "internal-wiki/shipping-rules.md",
    "app/services/order_events.py",
    "app/integrations/parcelly/client.py",
)
CORPUS: list[tuple[str, str]] = [
    ("allow", "We store the order weight as a decimal number of kilograms, e.g. 14.5."),
    ("allow", "Shipments heavier than 25 kg must be sent as pallets through a separate operation."),
    ("allow", "POST /v1/shipments returns 201 with id, status CREATED and tracking_number."),
    ("allow", "Missing Idempotency-Key returns 400 IDEMPOTENCY_KEY_REQUIRED for new partners."),
    ("allow", "Phone numbers must be in E.164 format, for example +48600100200."),
    ("allow", "Example receiver: Jan Kowalski, jan.kowalski@example.com, 600 100 200."),
    ("allow", "Webhooks carry an X-Parcelly-Signature header: sha256 HMAC of the raw body."),
    ("allow", "Statuses: CREATED, COLLECTED, IN_TRANSIT, DELIVERED, RETURNED."),
    ("allow", "The postcode prefix must match the city, otherwise 422 POSTCODE_CITY_MISMATCH."),
    ("allow", "Retries happen up to 5 times after 0.5s, 1s, 2s, 4s and 8s."),
    ("allow", "Size A up to 5000 g, B up to 15000 g, C up to 25000 g."),
    ("allow", "Our order ids look like ORD-1001; we send them as the shipment reference."),
    (
        "allow",
        "Address is one free-text line typed by the customer, e.g. ul. Długa 5/3, 00-001 Warszawa.",
    ),
    ("allow", "return the shipment id in the response body"),
    ("allow", "We call your API right after the payment is confirmed."),
    ("allow", "A shipment id looks like shp_1 and a tracking number like PCL1PL."),
    ("allow", "The partner API key is sent as Authorization: Bearer <key>."),
    ("allow", "Cancelled orders are never shipped."),
    ("block", "```python\ndef create_shipment(order):\n    return client.post(order)\n```"),
    ("block", "Use this:\n```\nPOST /v1/shipments\n```"),
    ("block", "def to_grams(kg):\n    return math.ceil(kg * 1000)"),
    ("block", "import httpx\nfrom app.models import Order"),
    ("block", "const size = weight <= 5000 ? 'A' : 'B';\nmodule.exports = { size };"),
    ("block", "function sign(body) {\n  return hmac(secret, body);\n}"),
    ("block", "if (weight > 25000) {\n  throw new ApiError(422);\n}"),
    ("block", "let total = 0;\nfor (const item of items) total += item.weight;"),
    ("block", "class OrderMapper:\n    def map(self, order): pass"),
    ("block", "async def handler(order):\n    await send(order)"),
    ("block", "Our key is sk-abcdefghijklmnopqrstuvwxyz123456"),
    ("block", "AWS access key AKIAABCDEFGHIJKLMNOP is used in staging"),
    ("block", "-----BEGIN RSA PRIVATE KEY-----\nMIIEow"),
    ("block", "Set NEBIUS_API_KEY before running the agent."),
    ("block", "Token: f3a9c1d27b8e4f6a9d0c2b7e5a1f8c3d6e9b2a4f"),
    ("block", "Contact our engineer at anna.nowak@realcompany.pl for access."),
    ("block", "Call the warehouse manager at +48 512 345 678."),
    ("block", 'Traceback (most recent call last):\n  File "app/main.py", line 3'),
    ("block", "Error at createShipment (src/shipments.js:42:13)"),
    ("redact", "The rule lives in internal-wiki/shipping-rules.md and applies to every partner."),
    ("redact", "We hook into app/services/order_events.py after payment."),
    ("redact", "Retries are implemented in src/webhooks.js with exponential backoff."),
    ("redact", "Our client in app/integrations/parcelly/client.py sends the key."),
    ("redact", "Pricing comes from src/shipments.js tables."),
]


def deterministic(text: str, allowlist: SyntheticAllowlist) -> str:
    verdict = check_message({"text": text}, allowlist, REPO_PATHS)
    if verdict.blocked:
        return "block"
    return "redact" if verdict.redactions else "allow"


async def classified(texts: list[str]) -> list[str]:
    llm = LLMClient.from_env()
    verdicts = [await classify(llm, {"text": text}, "eval") for text in texts]
    return [verdict.verdict for verdict in verdicts]


def main() -> None:
    parser = argparse.ArgumentParser(description="Check the egress corpus against the guard")
    parser.add_argument("--classifier", choices=["live", "record", "replay"])
    arguments = parser.parse_args()
    preset = parse_yaml(
        (REPO_ROOT / "demo" / "presets" / "spokes-parcelly.yaml").read_text("utf-8")
    )
    allowlist = SyntheticAllowlist.build(**preset["synthetic_allowlist"])
    mismatches = 0
    for expected, text in CORPUS:
        actual = deterministic(text, allowlist)
        if actual != expected:
            mismatches += 1
            print(f"MISMATCH expected {expected}, got {actual}: {text[:70]!r}")
    print(f"deterministic rules: {len(CORPUS) - mismatches}/{len(CORPUS)} as expected")
    if arguments.classifier:
        os.environ["DOUBLEBLIND_LLM_MODE"] = arguments.classifier
        os.environ["DOUBLEBLIND_CASSETTE"] = str(CASSETTE)
        verdicts = asyncio.run(classified([text for _, text in CORPUS]))
        disagreements = [
            (expected, verdict, text)
            for (expected, text), verdict in zip(CORPUS, verdicts, strict=True)
            if (verdict == "allow") != (expected == "allow")
        ]
        for expected, verdict, text in disagreements:
            print(f"classifier said {verdict}, corpus says {expected}: {text[:70]!r}")
        agreed = len(CORPUS) - len(disagreements)
        print(f"LLM classifier: {agreed}/{len(CORPUS)} agree on allow vs not allow")
    if mismatches:
        sys.exit(1)


if __name__ == "__main__":
    main()
