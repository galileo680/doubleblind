import time
from pathlib import Path

from broker_helpers import Broker, broker, run_payload, signed

from doubleblind_broker.e2e import E2ERequest, SandboxGatewayE2E
from doubleblind_contract.runtime import RunReport, ScenarioResult, utc_timestamp
from doubleblind_sandbox.local import LocalSandboxProvider
from doubleblind_sandbox.provider import ProbeResult

TOKEN_FACTORY_IMAGE = "3f0c9a52-7d1e-4b8a-9c61-2e5f8d4a7b90"


class RecordingE2E:
    def __init__(self) -> None:
        self.requests: list[E2ERequest] = []

    def run(self, request: E2ERequest) -> RunReport:
        self.requests.append(request)
        report = RunReport(request.contract_id, request.contract_version, "e2e", utc_timestamp())
        report.scenarios = [ScenarioResult("E1", "e2e", "passed", 1)]
        report.finished_at = utc_timestamp()
        return report


class FixedProbe:
    async def run(self) -> list[ProbeResult]:
        return [ProbeResult("dns", "DNS error", "DNS error", "passed")]


def wait_for_state(session: Broker, state: str) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if session.state() == state:
            return
        time.sleep(0.05)
    raise AssertionError(f"the session never reached {state}")


def test_green_test_runs_carry_workspace_images_into_the_e2e_request() -> None:
    executor = RecordingE2E()
    with broker(e2e_executor=executor) as session:
        contract = signed(session)
        consumer = {**run_payload(contract, "consumer"), "workspace_image": TOKEN_FACTORY_IMAGE}
        provider = {**run_payload(contract, "provider"), "workspace_image": "local:abc"}
        session.post("/test-runs", "agent_spokes", consumer, 201)
        session.post("/test-runs", "agent_parcelly", provider, 201)
        wait_for_state(session, "CERTIFIED")
        relay_after_e2e = session.client.get(
            f"/relay/{session.session_id}/spokes/next",
            params={"wait": 0},
            headers={"X-Relay-Token": executor.requests[0].relay_tokens["spokes"]},
        )

    request = executor.requests[0]
    assert request.images == {"spokes": TOKEN_FACTORY_IMAGE, "parcelly": "local:abc"}
    assert set(request.relay_tokens) == {"spokes", "parcelly"}
    assert relay_after_e2e.status_code == 401


def test_workspace_image_must_look_like_an_image_reference() -> None:
    with broker() as session:
        contract = signed(session)
        payload = {**run_payload(contract, "consumer"), "workspace_image": "rm -rf /; echo"}
        rejected = session.post("/test-runs", "agent_spokes", payload)

    assert rejected.status_code == 422


def test_isolation_probe_adds_sandbox_probes_and_emits_an_event() -> None:
    with broker() as session:
        session.service.isolation_probe = FixedProbe()
        signed(session)
        probe = session.post("/isolation-probe", "observer", status=200).json()
        events = [event for event in session.get("/events") if event["type"] == "isolation.probe"]

    assert probe["ok"] is True
    assert [result["name"] for result in probe["results"]][-1] == "dns"
    assert events[0]["payload"] == probe


def test_sandbox_e2e_needs_workspace_images(tmp_path: Path) -> None:
    executor = SandboxGatewayE2E(lambda: LocalSandboxProvider(tmp_path), "http://127.0.0.1:1")
    request = E2ERequest(
        session_id="ses_1",
        contract_yaml="",
        contract_id="c",
        contract_version=1,
        consumer_party="spokes",
        provider_party="parcelly",
        consumer_binding="",
        provider_binding="",
        secrets={},
        images={"spokes": "a"},
        relay_tokens={},
    )

    without_images = executor.run(request)

    assert (
        "no green workspace image for parcelly" in without_images.scenarios[0].failures[0].message
    )
