import json
import os
import subprocess
import sys
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

import httpx
from broker_helpers import PRESETS_DIR, REFERENCE, run_payload
from scripted_party import BRIEFS, reference_binding, round_one, round_two
from scripted_session import running_broker

from doubleblind_broker.db import build_engine
from doubleblind_broker.gateway import build_allowlist, path_regex, url_path
from doubleblind_broker.service import BrokerService, BrokerSettings
from doubleblind_contract.http_server import BackgroundServer, IncomingRequest, OutgoingResponse
from doubleblind_contract.loader import load_binding_text, load_contract_text, parse_yaml
from doubleblind_contract.models import Binding

SHIPMENT_REQUEST = {
    "reference": "ORD-1",
    "receiver": {
        "name": "Jan Kowalski",
        "phone": "+48600100200",
        "address": {
            "street": "Długa",
            "building": "5",
            "postcode": "00-001",
            "city": "Warszawa",
            "country": "PL",
        },
    },
    "parcel": {"weight_g": 1000, "size": "A"},
}


def binding(party: str) -> Binding:
    return load_binding_text(reference_binding(party))


def reference() -> Any:
    return load_contract_text(
        (
            PRESETS_DIR.parent.parent
            / "packages/contract/tests/fixtures/spokes_parcelly_signed.yaml"
        ).read_text(encoding="utf-8")
    )


def test_provider_allowlist_comes_from_operations_and_binding() -> None:
    allowlist = build_allowlist(reference(), binding("parcelly"), "provider")

    allowed = [
        ("POST", "/v1/shipments"),
        ("POST", "/v1/pallets"),
        ("POST", "/__sandbox/reset"),
        ("POST", "/__sandbox/shipments/shp_1/advance"),
        ("POST", "/__sandbox/shipments/by-reference/ORD-1/advance"),
        ("GET", "/v1/shipments"),
        ("GET", "/health"),
    ]
    assert all(allowlist.match(method, path) for method, path in allowed)
    assert allowlist.match("GET", "/admin/secrets") is None
    assert allowlist.match("DELETE", "/v1/shipments") is None
    assert allowlist.match("POST", "/__sandbox/shipments/a/b/advance") is None
    operation = allowlist.match("POST", "/v1/shipments")
    assert operation is not None and operation.source == "operation create_shipment"


def test_consumer_allowlist_comes_from_webhooks_and_binding() -> None:
    allowlist = build_allowlist(reference(), binding("spokes"), "consumer")

    assert allowlist.match("POST", "/integrations/parcelly/webhook") is not None
    assert allowlist.match("POST", "/orders/ORD-1001/pay") is not None
    assert allowlist.match("GET", "/orders/ORD-1001") is not None
    assert allowlist.match("POST", "/v1/shipments") is None
    assert build_allowlist(reference(), None, "consumer").calls == []


def test_path_helpers() -> None:
    assert url_path("${CONSUMER_BASE_URL}/integrations/parcelly/webhook") == (
        "/integrations/parcelly/webhook"
    )
    assert url_path("https://shop.example.com/hook") == "/hook"
    assert url_path("${BASE}") == "/"
    assert path_regex("/orders/{{ctx.order_id}}/pay").match("/orders/ORD-1/pay")
    assert path_regex("/v1/shipments/{id}").match("/v1/shipments/shp_1")
    assert not path_regex("/v1/shipments/{id}").match("/v1/shipments/a/b")


@dataclass
class Session:
    client: httpx.Client
    session_id: str
    tokens: dict[str, str]

    def as_party(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def post(self, path: str, who: str, body: Any = None) -> Any:
        response = self.client.post(
            f"/api/v1/sessions/{self.session_id}{path}", json=body, headers=self.as_party(who)
        )
        assert response.status_code < 400, response.text
        return response.json()

    def get(self, path: str, who: str = "observer") -> Any:
        response = self.client.get(
            f"/api/v1/sessions/{self.session_id}{path}", headers=self.as_party(who)
        )
        assert response.status_code < 400, response.text
        return response.json() if "json" in response.headers["content-type"] else response.text

    def turn(self, who: str, patches: list[dict[str, Any]], status: str = "continue") -> None:
        view = self.get("")
        self.post(
            "/turns",
            who,
            {
                "round": view["round"] + 1,
                "based_on_version": view["current_contract_version"],
                "message": "Propozycja.",
                "patches": patches,
                "status": status,
            },
        )


def signed_session(client: httpx.Client) -> Session:
    created = client.post("/api/v1/sessions", json={"name": "gw", "preset": "spokes-parcelly"})
    data = created.json()
    session = Session(client, data["session_id"], data["tokens"])
    session.post("/start", "observer")
    session.post("/briefs", "agent_spokes", BRIEFS["spokes"])
    session.post("/briefs", "agent_parcelly", BRIEFS["parcelly"])
    session.turn("agent_parcelly", round_one(parse_yaml(session.get("/contract")), REFERENCE))
    session.turn("agent_spokes", round_two(parse_yaml(session.get("/contract")), REFERENCE))
    session.turn("agent_parcelly", [], "ready_to_sign")
    session.turn("agent_spokes", [], "ready_to_sign")
    session.post("/signatures", "human_spokes", {"version": 3})
    session.post("/signatures", "human_parcelly", {"version": 3})
    contract = parse_yaml(session.get("/contract"))
    session.post("/test-runs", "agent_spokes", run_payload(contract, "consumer"))
    session.post("/test-runs", "agent_parcelly", run_payload(contract, "provider"))
    deadline = time.monotonic() + 10
    while session.get("")["state"] == "E2E" and time.monotonic() < deadline:
        time.sleep(0.05)
    return session


def courier_app(request: IncomingRequest) -> OutgoingResponse:
    if request.path == "/v1/shipments":
        body = json.loads(request.body)
        shipment = {"id": "shp_1", "reference": body["reference"], "status": "CREATED"}
        if body["reference"] != "ORD-INCOMPLETE":
            shipment["tracking_number"] = "PCL1PL"
        return OutgoingResponse.json(201, shipment)
    return OutgoingResponse.json(200, {"ok": True, "path": request.path})


def shop_app(request: IncomingRequest) -> OutgoingResponse:
    return OutgoingResponse.json(200, {"status": "accepted", "path": request.path})


@contextmanager
def relay(broker_url: str, session_id: str, party: str, token: str, target: str) -> Iterator[None]:
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "doubleblind_sandbox.relay_client",
            "--broker",
            broker_url,
            "--session",
            session_id,
            "--party",
            party,
            "--target",
            target,
            "--wait",
            "2",
        ],
        env={**os.environ, "DOUBLEBLIND_RELAY_TOKEN": token},
    )
    try:
        yield
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_gateway_relays_contract_traffic_through_relay_clients() -> None:
    settings = BrokerSettings(presets_dir=PRESETS_DIR, next_action_wait_s=0.0)
    service = BrokerService(build_engine("sqlite://"), settings)
    with (
        running_broker(service) as broker_url,
        httpx.Client(base_url=broker_url, timeout=30.0) as client,
        BackgroundServer(courier_app) as courier,
        BackgroundServer(shop_app) as shop,
    ):
        session = signed_session(client)
        sid = session.session_id
        tokens = {
            party: service.gateway.issue_token(sid, party) for party in ("parcelly", "spokes")
        }
        with (
            relay(broker_url, sid, "parcelly", tokens["parcelly"], courier.url),
            relay(broker_url, sid, "spokes", tokens["spokes"], shop.url),
        ):
            created = client.post(f"/gw/{sid}/parcelly/v1/shipments", json=SHIPMENT_REQUEST)
            incomplete = client.post(
                f"/gw/{sid}/parcelly/v1/shipments",
                json={**SHIPMENT_REQUEST, "reference": "ORD-INCOMPLETE"},
            )
            webhook = client.post(
                f"/gw/{sid}/spokes/integrations/parcelly/webhook", json={"status": "DELIVERED"}
            )
            advance = client.post(
                f"/gw/{sid}/parcelly/__sandbox/shipments/shp_1/advance",
                json={"to": "DELIVERED"},
            )
            forbidden = client.get(f"/gw/{sid}/parcelly/admin/secrets")
            wrong_party = client.post(f"/gw/{sid}/spokes/v1/shipments", json=SHIPMENT_REQUEST)
        bad_token = client.get(f"/relay/{sid}/parcelly/next", headers={"X-Relay-Token": "wrong"})
        events = [
            event["payload"]
            for event in session.get("/events")
            if event["type"] == "gateway.request"
        ]

    assert created.status_code == 201
    assert created.json()["tracking_number"] == "PCL1PL"
    assert incomplete.status_code == 201
    assert webhook.json() == {"status": "accepted", "path": "/integrations/parcelly/webhook"}
    assert advance.json()["path"] == "/__sandbox/shipments/shp_1/advance"
    assert forbidden.status_code == 403
    assert forbidden.json()["code"] == "NOT_IN_CONTRACT"
    assert wrong_party.status_code == 403
    assert bad_token.status_code == 401
    assert [event["status"] for event in events] == [201, 201, 200, 200, 403, 403]
    assert events[0]["violations"] == []
    assert events[1]["violations"] == ["response: 'tracking_number' is a required property"]
    assert events[0]["source"] == "operation create_shipment"
    assert events[4]["allowed"] is False


def test_gateway_answers_504_without_a_relay_and_403_before_signing() -> None:
    settings = BrokerSettings(presets_dir=PRESETS_DIR, next_action_wait_s=0.0)
    service = BrokerService(build_engine("sqlite://"), settings)
    service.gateway.timeout_s = 0.3
    with running_broker(service) as broker_url, httpx.Client(base_url=broker_url) as client:
        unsigned = client.post("/api/v1/sessions", json={"name": "x", "preset": "spokes-parcelly"})
        early = client.post(f"/gw/{unsigned.json()['session_id']}/parcelly/v1/shipments", json={})
        session = signed_session(client)
        silent = client.post(
            f"/gw/{session.session_id}/parcelly/v1/shipments", json=SHIPMENT_REQUEST
        )
        started = time.monotonic()
        idle = client.get(
            f"/relay/{session.session_id}/parcelly/next",
            params={"wait": 0.2},
            headers={"X-Relay-Token": service.gateway.issue_token(session.session_id, "parcelly")},
        )

    assert early.status_code == 403
    assert silent.status_code == 504
    assert silent.json()["code"] == "RELAY_TIMEOUT"
    assert idle.status_code == 204
    assert time.monotonic() - started < 5
