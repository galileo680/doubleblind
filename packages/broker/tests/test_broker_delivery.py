import json
from typing import Any

from broker_helpers import Broker, broker, run_payload, signed
from fastapi.testclient import TestClient
from sqlmodel import select

from doubleblind_broker.db import EventRecord, open_session
from doubleblind_broker.e2e import E2ERequest, UnavailableE2E
from doubleblind_contract.runtime import Failure, RunReport, ScenarioResult, utc_timestamp


class ScriptedE2E:
    def __init__(self, outcomes: list[bool]) -> None:
        self.outcomes = outcomes
        self.requests: list[E2ERequest] = []

    def run(self, request: E2ERequest) -> RunReport:
        self.requests.append(request)
        passed = self.outcomes.pop(0)
        report = RunReport(request.contract_id, request.contract_version, "e2e", utc_timestamp())
        failures = [] if passed else [Failure(5, "status", "delivered", "paid", "value mismatch")]
        report.scenarios = [
            ScenarioResult(
                "E1", "Paid order is delivered", "passed" if passed else "failed", 9, failures
            )
        ]
        return report


def wait(session: Broker) -> None:
    session.client.portal.call(session.service.wait_for_background)


def both_ready(session: Broker, contract: dict[str, Any]) -> None:
    session.post("/test-runs", "agent_spokes", run_payload(contract, "consumer"), 201)
    session.post("/test-runs", "agent_parcelly", run_payload(contract, "provider"), 201)
    wait(session)


def test_party_is_ready_only_with_all_green_tests_and_a_binding() -> None:
    with broker() as session:
        contract = signed(session)
        green_without_binding = session.post(
            "/test-runs", "agent_spokes", run_payload(contract, "consumer", binding=False), 201
        ).json()
        red_with_binding = session.post(
            "/test-runs", "agent_spokes", run_payload(contract, "consumer", passed=False), 201
        ).json()
        action = session.get("/next-action", "agent_spokes")["action"]
        green_again = session.post(
            "/test-runs", "agent_spokes", run_payload(contract, "consumer", binding=False), 201
        ).json()

    assert green_without_binding == {"ready_for_e2e": False}
    assert red_with_binding == {"ready_for_e2e": False}
    assert action == "implement"
    assert green_again == {"ready_for_e2e": True}


def test_test_runs_are_checked_before_they_are_accepted() -> None:
    with broker() as session:
        contract = signed(session)
        wrong_side = session.post("/test-runs", "agent_spokes", run_payload(contract, "provider"))
        stale = session.post(
            "/test-runs",
            "agent_spokes",
            {**run_payload(contract, "consumer"), "contract_version": 2},
        )
        leaked = session.post(
            "/test-runs",
            "agent_spokes",
            {
                **run_payload(contract, "consumer"),
                "results": [
                    {
                        "scenario_id": "S1",
                        "status": "failed",
                        "sanitized_reason": "Traceback (most recent call last): boom",
                    }
                ],
            },
        )
        bad_binding = session.post(
            "/test-runs",
            "agent_spokes",
            {**run_payload(contract, "consumer"), "binding_yaml": "side: shop\n"},
        )

    assert wrong_side.json()["code"] == "WRONG_SIDE"
    assert stale.status_code == 409
    assert leaked.json()["code"] == "EGRESS_BLOCKED"
    assert bad_binding.json()["code"] == "INVALID_BINDING"


def test_green_sides_lead_to_a_certified_session() -> None:
    executor = ScriptedE2E([True])
    with broker(e2e_executor=executor) as session:
        contract = signed(session)
        both_ready(session, contract)
        state = session.state()
        certificate = session.get("/certificate")
        done = session.get("/next-action", "agent_parcelly")

    assert state == "CERTIFIED"
    request = executor.requests[0]
    assert (request.consumer_party, request.provider_party) == ("spokes", "parcelly")
    assert set(request.secrets) == {"partner_api_key", "webhook_secret"}
    assert certificate["certified"] is True
    assert certificate["signed_contract_version"] == 3
    assert set(certificate["results"]) == {"consumer", "provider", "e2e"}
    assert certificate["audit"]["ok"] is True
    assert done["action"] == "done"


def test_red_end_to_end_run_sends_both_parties_back_with_feedback() -> None:
    executor = ScriptedE2E([False, True])
    with broker(e2e_executor=executor) as session:
        contract = signed(session)
        both_ready(session, contract)
        state = session.state()
        action = session.get("/next-action", "agent_spokes")
        both_ready(session, contract)
        final = session.state()

    assert state == "IMPLEMENTING"
    assert action["action"] == "fix_after_e2e"
    failure = action["context"]["sanitized_e2e"]["scenarios"][0]["failures"][0]
    assert failure["message"] == "value mismatch"
    assert "app_log_tail" not in action["context"]["sanitized_e2e"]["scenarios"][0]
    assert final == "CERTIFIED"


def test_missing_executor_reports_an_error_instead_of_certifying() -> None:
    with broker(e2e_executor=UnavailableE2E()) as session:
        contract = signed(session)
        both_ready(session, contract)

        assert session.state() == "IMPLEMENTING"
        assert session.post("/e2e/run", "observer").status_code == 409


def test_contract_gap_reopens_negotiation_for_the_reporter() -> None:
    with broker() as session:
        signed(session)
        session.post(
            "/contract-gap",
            "agent_spokes",
            {"scenario_ids": ["S1"], "description": "Brakuje zachowania dla wagi 0 g."},
            201,
        )
        action = session.get("/next-action", "agent_spokes")
        view = session.get("")

    assert view["state"] == "AMENDING"
    assert action["action"] == "amend"
    assert action["context"]["based_on_version"] == 3


def test_audit_chain_detects_tampering() -> None:
    with broker() as session:
        signed(session)
        assert session.get("/audit/verify")["ok"] is True
        with open_session(session.service.engine) as db:
            event = db.exec(select(EventRecord).where(EventRecord.type == "turn.accepted")).first()
            assert event is not None
            payload = json.loads(event.payload_json)
            payload["message"] = "Something else."
            event.payload_json = json.dumps(payload)
            db.add(event)
            db.commit()
            tampered_seq = event.seq
        audit = session.get("/audit/verify")

    assert audit["ok"] is False
    assert audit["broken_at"] == tampered_seq


def stream_events(client: TestClient, session: Broker, who: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    with client.stream(
        "GET",
        session.url("/events/stream"),
        headers=session.headers(who),
        params={"follow": "false"},
    ) as response:
        for line in response.iter_lines():
            if line.startswith("data:"):
                events.append(json.loads(line.removeprefix("data:").strip()))
    return events


def test_event_stream_respects_visibility() -> None:
    with broker() as session:
        signed(session)
        blocked = session.post(
            "/contract-gap",
            "agent_spokes",
            {"description": "Traceback (most recent call last): our parser crashed"},
        )
        spokes_view = stream_events(session.client, session, "agent_spokes")
        parcelly_view = stream_events(session.client, session, "agent_parcelly")
        observer_view = stream_events(session.client, session, "observer")
        token_in_query = session.client.get(
            session.url("/events"), params={"token": session.tokens["human_parcelly"]}
        )

    assert blocked.status_code == 422
    assert "egress.blocked" in {event["type"] for event in spokes_view}
    assert "egress.blocked" not in {event["type"] for event in parcelly_view}
    assert {event["visibility"] for event in spokes_view} <= {"shared", "spokes"}
    assert {event["visibility"] for event in parcelly_view} <= {"shared", "parcelly"}
    assert "observer" in {event["visibility"] for event in observer_view}
    assert len(observer_view) > len(spokes_view)
    assert [event["seq"] for event in observer_view] == sorted(
        event["seq"] for event in observer_view
    )
    assert token_in_query.status_code == 200


def test_observer_only_actions() -> None:
    with broker() as session:
        probe = session.post("/isolation-probe", "observer")
        agent_probe = session.post("/isolation-probe", "agent_spokes")

    assert probe.status_code == 200
    assert probe.json()["ok"] is True
    assert [result["status"] for result in probe.json()["results"]] == ["passed", "passed"]
    assert {result["actual"] for result in probe.json()["results"]} == {"403"}
    assert agent_probe.status_code == 403
