import json
import re
from typing import Any

import httpx
from contract_fixtures import MINIMAL

from doubleblind_contract.http_server import (
    BackgroundServer,
    IncomingRequest,
    OutgoingResponse,
    header_value,
)
from doubleblind_contract.loader import load_binding_text, load_contract_text
from doubleblind_contract.models import Binding, Contract
from doubleblind_contract.signing import sign, verify

SECRETS = {"partner_api_key": "partner-key", "webhook_secret": "hook-secret"}
ORDER_PATH = re.compile(r"^/orders/(?P<id>[^/]+)(?P<pay>/pay)?$")


def contract_with_scenarios(scenarios_yaml: str) -> Contract:
    text = MINIMAL.read_text(encoding="utf-8")
    head = text[: text.index("scenarios:\n")]
    return load_contract_text(head + "scenarios:\n" + scenarios_yaml)


class FakeShop:
    def __init__(self, stub_url: str = "", weight_factor: int = 1000) -> None:
        self.stub_url = stub_url
        self.weight_factor = weight_factor
        self.send_weight = True
        self.orders: dict[str, dict[str, Any]] = {}
        self.server = BackgroundServer(self.handle)

    @property
    def url(self) -> str:
        return self.server.url

    def __enter__(self) -> "FakeShop":
        self.server.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.server.stop()

    def binding(self) -> Binding:
        return load_binding_text(
            f"""
doubleblind_binding: "1.0"
side: consumer
base_url: "{self.url}"
webhook_receiver_url: "{self.url}/hook"
actions:
  reset: {{ method: POST, path: /reset }}
  create_order:
    {{ method: POST, path: /orders, body: "{{{{args}}}}", capture: {{ order_id: "$.id" }} }}
  pay_order: {{ method: POST, path: "/orders/{{{{ctx.order_id}}}}/pay" }}
  broken_capture:
    {{ method: POST, path: /orders, body: {{}}, capture: {{ order_id: "$.nothing" }} }}
queries:
  order: {{ method: GET, path: "/orders/{{{{ctx.order_id}}}}" }}
"""
        )

    def handle(self, request: IncomingRequest) -> OutgoingResponse:
        body = json.loads(request.body) if request.body else {}
        if request.path == "/reset":
            self.orders.clear()
            return OutgoingResponse.json(200, {})
        if request.path == "/orders" and request.method == "POST":
            order_id = f"ORD-{len(self.orders) + 1}"
            self.orders[order_id] = {"id": order_id, "status": "pending", **body}
            return OutgoingResponse.json(201, {"id": order_id})
        if request.path == "/hook":
            return self.receive_webhook(request, body)
        found = ORDER_PATH.match(request.path)
        if found is None or found["id"] not in self.orders:
            return OutgoingResponse.json(404, {"code": "NOT_FOUND"})
        order = self.orders[found["id"]]
        if found["pay"]:
            order["status"] = "paid"
            payload: dict[str, Any] = {"reference": order["id"]}
            if self.send_weight:
                payload["weight_g"] = int(order.get("weight_kg", 1) * self.weight_factor)
            httpx.post(
                f"{self.stub_url}/v1/shipments",
                json=payload,
                headers={"Authorization": f"Bearer {SECRETS['partner_api_key']}"},
                timeout=5.0,
            )
        return OutgoingResponse.json(200, order)

    def receive_webhook(self, request: IncomingRequest, body: dict[str, Any]) -> OutgoingResponse:
        if not verify(
            SECRETS["webhook_secret"], request.body, header_value(request.headers, "X-Signature")
        ):
            return OutgoingResponse.json(401, {"code": "INVALID_SIGNATURE"})
        for order in self.orders.values():
            if order.get("shipment_id") == body.get("shipment_id"):
                order["status"] = body["status"].lower()
        return OutgoingResponse.json(200, {"status": "accepted"})


class FakeCourier:
    def __init__(self) -> None:
        self.webhook_url = ""
        self.complete_shipments = True
        self.sign_with = SECRETS["webhook_secret"]
        self.server = BackgroundServer(self.handle)

    @property
    def url(self) -> str:
        return self.server.url

    def __enter__(self) -> "FakeCourier":
        self.server.start()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.server.stop()

    def binding(self) -> Binding:
        return load_binding_text(
            f"""
doubleblind_binding: "1.0"
side: provider
base_url: "{self.url}"
actions:
  reset: {{ method: POST, path: /reset }}
  register_webhook: {{ method: POST, path: /webhooks, body: "{{{{args}}}}" }}
  advance: {{ method: POST, path: /advance, body: "{{{{args}}}}" }}
"""
        )

    def handle(self, request: IncomingRequest) -> OutgoingResponse:
        body = json.loads(request.body) if request.body else {}
        if request.path == "/reset":
            return OutgoingResponse.json(200, {})
        if request.path == "/webhooks":
            self.webhook_url = body["url"]
            return OutgoingResponse.json(201, {})
        if request.path == "/v1/shipments":
            if (
                header_value(request.headers, "Authorization")
                != f"Bearer {SECRETS['partner_api_key']}"
            ):
                return OutgoingResponse.json(401, {"code": "UNAUTHORIZED"})
            shipment: dict[str, Any] = {"id": "shp_1", "reference": body.get("reference")}
            if self.complete_shipments:
                shipment["status"] = "CREATED"
            return OutgoingResponse.json(201, shipment)
        if request.path == "/advance":
            event = {
                "event": "shipment.status_changed",
                "shipment_id": "shp_1",
                "status": body["to"],
            }
            raw = json.dumps(event)
            httpx.post(
                self.webhook_url,
                content=raw,
                headers={"X-Signature": sign(self.sign_with, raw)},
                timeout=5.0,
            )
            return OutgoingResponse.json(200, {})
        return OutgoingResponse.json(404, {"code": "NOT_FOUND"})
