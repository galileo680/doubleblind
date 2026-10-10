from collections.abc import Iterator

import httpx
import pytest
from runner_fakes import SECRETS, FakeCourier, FakeShop, contract_with_scenarios

from doubleblind_contract.binding import PartyClient
from doubleblind_contract.capture_server import WebhookCapture
from doubleblind_contract.models import Contract
from doubleblind_contract.runtime import ScenarioResult
from doubleblind_contract.steps import RunEnvironment, ScenarioExecutor
from doubleblind_contract.stub_server import ProviderStub

PAID_ORDER = """
      - do: { party: consumer, action: create_order, args: { weight_kg: 1.5 } }
      - do: { party: consumer, action: pay_order }
"""


def consumer_scenario(steps: str) -> Contract:
    return contract_with_scenarios(
        f"""  - id: S1
    title: "Consumer scenario"
    side: consumer
    covers: [D1]
    steps:
{steps}"""
    )


def provider_scenario(steps: str) -> Contract:
    return contract_with_scenarios(
        f"""  - id: P1
    title: "Provider scenario"
    side: provider
    covers: [D1]
    steps:
{steps}"""
    )


@pytest.fixture
def http() -> Iterator[httpx.Client]:
    with httpx.Client(timeout=5.0) as client:
        yield client


def run_consumer(contract: Contract, http: httpx.Client, shop: FakeShop) -> ScenarioResult:
    with ProviderStub(contract, SECRETS, f"{shop.url}/hook") as stub:
        shop.stub_url = stub.url
        environment = RunEnvironment(
            contract=contract,
            side="consumer",
            http=http,
            secrets=SECRETS,
            env={},
            parties={"consumer": PartyClient(shop.binding(), http)},
            stub=stub,
            urls={"stub": {"url": stub.url}},
        )
        return ScenarioExecutor(environment, contract.scenarios[0]).run()


def run_provider(contract: Contract, http: httpx.Client, courier: FakeCourier) -> ScenarioResult:
    with WebhookCapture() as capture:
        environment = RunEnvironment(
            contract=contract,
            side="provider",
            http=http,
            secrets=SECRETS,
            env={},
            parties={"provider": PartyClient(courier.binding(), http)},
            capture=capture,
            provider_base_url=courier.url,
            urls={"capture": {"url": capture.url}},
        )
        return ScenarioExecutor(environment, contract.scenarios[0]).run()


def messages(result: ScenarioResult) -> list[str]:
    return [failure.message for failure in result.failures]


def test_consumer_scenario_passes_when_the_shop_follows_the_contract(http: httpx.Client) -> None:
    contract = consumer_scenario(
        PAID_ORDER + "      - expect_received: { operation: create_shipment, count: 1, within_s: 2,"
        " match: { body.weight_g: 1500, body.reference: '{{ctx.order_id}}' } }\n"
    )

    with FakeShop() as shop:
        result = run_consumer(contract, http, shop)

    assert result.status == "passed", messages(result)


def test_a_wrong_value_is_reported_with_its_path(http: httpx.Client) -> None:
    contract = consumer_scenario(
        PAID_ORDER + "      - expect_received: { operation: create_shipment, within_s: 1,"
        " match: { body.weight_g: 1500 } }\n"
    )

    with FakeShop(weight_factor=10) as shop:
        result = run_consumer(contract, http, shop)

    assert result.status == "failed"
    failure = result.failures[0]
    assert (failure.step, failure.path, failure.expected, failure.actual) == (
        2,
        "body.weight_g",
        1500,
        15,
    )


def test_missing_request_is_reported(http: httpx.Client) -> None:
    contract = consumer_scenario(
        "      - do: { party: consumer, action: create_order, args: { weight_kg: 1 } }\n"
        "      - expect_received: { operation: create_shipment, within_s: 0.2 }\n"
    )

    with FakeShop() as shop:
        result = run_consumer(contract, http, shop)

    assert messages(result) == ["no create_shipment request was received"]


def test_unexpected_request_fails_expect_no_request(http: httpx.Client) -> None:
    contract = consumer_scenario(
        PAID_ORDER + "      - expect_no_request: { operation: create_shipment, within_s: 0.5 }\n"
    )

    with FakeShop() as shop:
        result = run_consumer(contract, http, shop)

    assert "was received but none was expected" in messages(result)[0]


def test_no_request_passes_when_nothing_arrives(http: httpx.Client) -> None:
    contract = consumer_scenario(
        "      - do: { party: consumer, action: create_order, args: { weight_kg: 1 } }\n"
        "      - expect_no_request: { within_s: 0.2 }\n"
    )

    with FakeShop() as shop:
        assert run_consumer(contract, http, shop).status == "passed"


def test_requests_that_break_the_contract_are_reported_automatically(http: httpx.Client) -> None:
    contract = consumer_scenario(PAID_ORDER)

    with FakeShop() as shop:
        shop.send_weight = False
        result = run_consumer(contract, http, shop)

    assert result.status == "failed"
    assert result.failures[0].step is None
    assert "request violated the contract: body: 'weight_g' is a required property" in messages(
        result
    )


def test_count_and_distinct_are_checked(http: httpx.Client) -> None:
    contract = consumer_scenario(
        PAID_ORDER + "      - do: { party: consumer, action: pay_order }\n"
        "      - expect_received: { operation: create_shipment, count: 1, within_s: 0.3,"
        " distinct: { body.weight_g: 2 } }\n"
    )

    with FakeShop() as shop:
        result = run_consumer(contract, http, shop)

    assert messages(result) == [
        "expected 1 create_shipment request(s)",
        "unexpected number of distinct values",
    ]


def test_emit_checks_the_consumer_answer(http: httpx.Client) -> None:
    contract = consumer_scenario(
        "      - emit: { webhook: status_changed, signature: valid,"
        " body: { event: shipment.status_changed, shipment_id: x, status: DELIVERED },"
        " expect: { status: 200 } }\n"
        "      - emit: { webhook: status_changed, signature: invalid,"
        " body: { event: shipment.status_changed, shipment_id: x, status: DELIVERED },"
        " expect: { status: 401 } }\n"
        "      - emit: { webhook: status_changed, signature: missing,"
        " body: { event: shipment.status_changed, shipment_id: x, status: DELIVERED },"
        " expect: { status: 200 } }\n"
    )

    with FakeShop() as shop:
        result = run_consumer(contract, http, shop)

    assert result.status == "failed"
    assert result.failures[0].step == 2
    assert result.failures[0].actual == 401


def test_expect_state_waits_until_the_value_appears(http: httpx.Client) -> None:
    contract = consumer_scenario(
        PAID_ORDER
        + "      - expect_state: { party: consumer, query: order, match: { status: paid },"
        " eventually: { timeout_s: 1, interval_s: 0.1 } }\n"
        "      - expect_state: { party: consumer, query: order, match: { status: shipped } }\n"
    )

    with FakeShop() as shop:
        result = run_consumer(contract, http, shop)

    assert result.failures[0].step == 3
    assert (result.failures[0].path, result.failures[0].actual) == ("status", "paid")


def test_template_and_binding_problems_are_failures_not_crashes(http: httpx.Client) -> None:
    missing_ctx = consumer_scenario("      - do: { party: consumer, action: pay_order }\n")
    unknown_action = consumer_scenario("      - do: { party: consumer, action: refund }\n")
    broken_capture = consumer_scenario("      - do: { party: consumer, action: broken_capture }\n")

    with FakeShop() as shop:
        results = [
            run_consumer(contract, http, shop)
            for contract in (missing_ctx, unknown_action, broken_capture)
        ]

    assert "'ctx' has no field 'order_id'" in messages(results[0])[0]
    assert messages(results[1]) == ["action 'refund' is not defined in the consumer binding"]
    assert messages(results[2]) == ["capture order_id: $.nothing not found in the response"]
    assert {result.status for result in results} == {"failed"}


def test_failed_action_reports_status_and_body(http: httpx.Client) -> None:
    contract = consumer_scenario(
        "      - do: { party: consumer, action: create_order, args: { weight_kg: 1 } }\n"
        "      - expect_state: { party: consumer, query: order, match: { status: paid } }\n"
    )

    with FakeShop() as shop:
        result = run_consumer(contract, http, shop)

    assert result.failures[0].path == "status"


def test_unreachable_app_gives_an_error_status(http: httpx.Client) -> None:
    contract = consumer_scenario("      - do: { party: consumer, action: reset }\n")
    shop = FakeShop()
    shop.server.start()
    shop.server.stop()

    result = run_consumer(contract, http, shop)

    assert result.status == "error"
    assert "ConnectError" in messages(result)[0]


def test_provider_send_checks_status_schema_match_and_capture(http: httpx.Client) -> None:
    contract = provider_scenario(
        "      - send: { operation: create_shipment,"
        " headers: { Authorization: 'Bearer {{secrets.partner_api_key}}' },"
        " body: { reference: ORD-1, weight_g: 10 },"
        " expect: { status: 201, match: { body.reference: ORD-1 } },"
        " capture: { shipment_id: '$.id' } }\n"
        "      - send: { operation: create_shipment,"
        " headers: { Authorization: 'Bearer {{secrets.partner_api_key}}' },"
        " body: { reference: '{{ctx.shipment_id}}', weight_g: 10 },"
        " expect: { status: 201, match: { body.reference: shp_1 } } }\n"
    )

    with FakeCourier() as courier:
        complete = run_provider(contract, http, courier)
        courier.complete_shipments = False
        incomplete = run_provider(contract, http, courier)

    assert complete.status == "passed", messages(complete)
    assert incomplete.failures[0].path == "body"
    assert "'status' is a required property" in incomplete.failures[0].message


def test_provider_send_reports_an_undeclared_status(http: httpx.Client) -> None:
    contract = provider_scenario(
        "      - send: { operation: create_shipment, body: { reference: ORD-1, weight_g: 10 },"
        " expect: { status: 201 } }\n"
    )

    with FakeCourier() as courier:
        result = run_provider(contract, http, courier)

    assert [failure.message.split(":")[0] for failure in result.failures] == [
        "unexpected status from create_shipment",
        "status is not declared in the contract responses",
    ]


def test_expect_emitted_checks_count_match_schema_and_signature(http: httpx.Client) -> None:
    steps = (
        "      - do: { party: provider, action: register_webhook,"
        " args: { url: '{{capture.url}}' } }\n"
        "      - do: { party: provider, action: advance, args: { to: DELIVERED } }\n"
        "      - expect_emitted: { webhook: status_changed, count: 1, signature: valid,"
        " within_s: 2, match: { body.status: DELIVERED } }\n"
    )
    contract = provider_scenario(steps)

    with FakeCourier() as courier:
        good = run_provider(contract, http, courier)
        courier.sign_with = "wrong-secret"
        badly_signed = run_provider(contract, http, courier)

    assert good.status == "passed", messages(good)
    assert badly_signed.failures[0].path == "headers.X-Signature"


def test_expect_emitted_without_a_matching_webhook(http: httpx.Client) -> None:
    contract = provider_scenario(
        "      - do: { party: provider, action: register_webhook,"
        " args: { url: '{{capture.url}}' } }\n"
        "      - do: { party: provider, action: advance, args: { to: CREATED } }\n"
        "      - expect_emitted: { webhook: status_changed, within_s: 0.3,"
        " match: { body.status: DELIVERED } }\n"
    )
    nothing = provider_scenario(
        "      - expect_emitted: { webhook: status_changed, within_s: 0.2 }\n"
    )

    with FakeCourier() as courier:
        wrong = run_provider(contract, http, courier)
        empty = run_provider(nothing, http, courier)

    assert (wrong.failures[0].path, wrong.failures[0].actual) == ("body.status", "CREATED")
    assert messages(empty) == ["no webhook was received"]


def test_steps_that_need_missing_servers_fail_clearly(http: httpx.Client) -> None:
    contract = consumer_scenario("      - expect_received: { operation: create_shipment }\n")
    environment = RunEnvironment(contract=contract, side="consumer", http=http, secrets={}, env={})

    result = ScenarioExecutor(environment, contract.scenarios[0]).run()

    assert messages(result) == ["this step needs the provider stub"]
