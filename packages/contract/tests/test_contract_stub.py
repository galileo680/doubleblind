import json
from collections.abc import Iterator

import httpx
import pytest
from contract_fixtures import MINIMAL, SIGNED
from runner_fakes import SECRETS

from doubleblind_contract.capture_server import WebhookCapture
from doubleblind_contract.http_server import header_value
from doubleblind_contract.loader import load_contract, load_contract_text
from doubleblind_contract.runtime import ConfigurationError
from doubleblind_contract.signing import verify
from doubleblind_contract.stub_server import ProviderStub, path_pattern

AUTH = {"Authorization": "Bearer partner-key"}
SHIPMENT = {"reference": "ORD-1", "weight_g": 1500}


@pytest.fixture
def stub() -> Iterator[ProviderStub]:
    with ProviderStub(load_contract(MINIMAL), SECRETS) as running:
        yield running


def test_answers_with_the_rendered_stub_body(stub: ProviderStub) -> None:
    first = httpx.post(f"{stub.url}/v1/shipments", json=SHIPMENT, headers=AUTH)
    second = httpx.post(
        f"{stub.url}/v1/shipments", json={**SHIPMENT, "reference": "ORD-2"}, headers=AUTH
    )

    assert first.status_code == 201
    assert first.json() == {
        "id": "shp_1",
        "reference": "ORD-1",
        "status": "CREATED",
        "price_gr": 1599,
    }
    assert second.json()["id"] == "shp_2"
    assert [entry.operation for entry in stub.log()] == ["create_shipment", "create_shipment"]


def test_rejects_requests_that_break_the_contract(stub: ProviderStub) -> None:
    no_auth = httpx.post(f"{stub.url}/v1/shipments", json=SHIPMENT)
    bad_body = httpx.post(f"{stub.url}/v1/shipments", json={"reference": "ORD-1"}, headers=AUTH)
    not_json = httpx.post(f"{stub.url}/v1/shipments", content=b"{oops", headers=AUTH)

    assert no_auth.status_code == 422
    assert no_auth.json() == {
        "code": "CONTRACT_VIOLATION",
        "message": "missing required header Authorization",
    }
    assert bad_body.json()["message"] == "body: 'weight_g' is a required property"
    assert not_json.json()["message"] == "request body is not valid JSON"
    assert [entry.violations != [] for entry in stub.log()] == [True, True, True]


def test_checks_header_patterns() -> None:
    contract = load_contract(SIGNED)
    with ProviderStub(contract, SECRETS) as running:
        response = httpx.post(
            f"{running.url}/v1/shipments",
            json={},
            headers={"Authorization": "Token x", "Idempotency-Key": "k"},
        )

    assert response.json()["message"] == "header Authorization does not match /^Bearer .+$/"


def test_unknown_routes_are_logged_as_violations(stub: ProviderStub) -> None:
    response = httpx.get(f"{stub.url}/v1/unknown")

    assert response.status_code == 404
    assert stub.log()[0].operation is None
    assert "no operation in the contract matches GET /v1/unknown" in stub.log()[0].violations


def test_repeated_idempotency_key_replays_the_first_answer() -> None:
    contract = load_contract(SIGNED)
    headers = {**AUTH, "Idempotency-Key": "ORD-1"}
    body = {
        "reference": "ORD-1",
        "receiver": {
            "name": "Jan",
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
    with ProviderStub(contract, SECRETS) as running:
        first = httpx.post(f"{running.url}/v1/shipments", json=body, headers=headers).json()
        replay = httpx.post(f"{running.url}/v1/shipments", json=body, headers=headers).json()
        other = httpx.post(
            f"{running.url}/v1/shipments",
            json=body,
            headers={**AUTH, "Idempotency-Key": "ORD-2"},
        ).json()
        log = running.log()

    assert replay == first
    assert other["id"] == "shp_2"
    assert [entry.replayed for entry in log] == [False, True, False]


def test_control_endpoints_show_and_clear_the_log(stub: ProviderStub) -> None:
    httpx.post(f"{stub.url}/v1/shipments", json=SHIPMENT, headers=AUTH)

    log = httpx.get(f"{stub.url}/__stub/log").json()
    httpx.post(f"{stub.url}/__stub/reset")
    after = httpx.post(f"{stub.url}/v1/shipments", json=SHIPMENT, headers=AUTH)

    assert log[0]["operation"] == "create_shipment"
    assert log[0]["body"] == SHIPMENT
    assert after.json()["id"] == "shp_1"
    assert httpx.get(f"{stub.url}/__stub/nothing").status_code == 404


def test_emits_signed_webhooks_to_the_consumer() -> None:
    contract = load_contract(MINIMAL)
    event = {"event": "shipment.status_changed", "shipment_id": "shp_1", "status": "DELIVERED"}
    with WebhookCapture() as capture, ProviderStub(contract, SECRETS, capture.url) as running:
        assert running.emit("status_changed", event, "valid")[0] == 200
        running.emit("status_changed", event, "invalid")
        running.emit("status_changed", event, "missing")
        control = httpx.post(
            f"{running.url}/__stub/emit/status_changed", json={"body": event, "signature": "valid"}
        )
        received = capture.webhooks()

    assert control.json()["status"] == 200
    assert json.loads(received[0].raw_body) == event
    assert verify(
        "hook-secret", received[0].raw_body, header_value(received[0].headers, "X-Signature")
    )
    assert not verify(
        "hook-secret", received[1].raw_body, header_value(received[1].headers, "X-Signature")
    )
    assert header_value(received[2].headers, "X-Signature") is None


def test_emit_needs_a_known_webhook_and_a_receiver(stub: ProviderStub) -> None:
    with pytest.raises(ConfigurationError, match="does not exist"):
        stub.emit("lost_parcel", {}, "valid")
    with pytest.raises(ConfigurationError, match="no webhook_receiver_url"):
        stub.emit("status_changed", {}, "valid")


def test_stub_template_errors_and_missing_stubs_are_reported() -> None:
    text = MINIMAL.read_text(encoding="utf-8")
    broken_template = text.replace('"{{request.body.reference}}"', '"{{request.body.missing}}"')
    without_stub = text.replace("    stub:\n      default_status: 201\n", "    nostub:\n").replace(
        "\n      body: { id:", "\n      nobody: { id:"
    )
    head = without_stub.split("    nostub:\n")[0]
    without_stub = head + "webhooks:" + without_stub.split("webhooks:")[1]

    with ProviderStub(load_contract_text(broken_template), SECRETS) as running:
        template_error = httpx.post(f"{running.url}/v1/shipments", json=SHIPMENT, headers=AUTH)
    with ProviderStub(load_contract_text(without_stub), SECRETS) as running:
        no_stub = httpx.post(f"{running.url}/v1/shipments", json=SHIPMENT, headers=AUTH)

    assert template_error.status_code == 500
    assert template_error.json()["code"] == "STUB_TEMPLATE_ERROR"
    assert no_stub.status_code == 501


def test_path_parameters_match_one_segment() -> None:
    pattern = path_pattern("/v1/shipments/{id}/advance")

    assert pattern.match("/v1/shipments/shp_1/advance")
    assert not pattern.match("/v1/shipments/shp_1/x/advance")
    assert not pattern.match("/v1/shipments")
