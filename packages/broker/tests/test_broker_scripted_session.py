from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
from demo_workspace import Workspace, build_workspace
from scripted_party import ContractTestsInWorkspace
from scripted_session import (
    SessionOutcome,
    run_full_session,
    run_sandbox_session,
    run_session,
)

from doubleblind_broker.e2e import LocalProcessE2E

WEBHOOK_ROUTE = ("spokes/app/main.py", "    application.include_router(webhook.router)\n", "")


class WebhookOffAfterFirstTests:
    def __init__(self, workspace: Workspace) -> None:
        self.workspace = workspace
        self.inner = ContractTestsInWorkspace(workspace.spokes)
        self.broken = ExitStack()
        self.runs = 0

    def implement(self, contract_yaml: str, secrets: Any, side: Any) -> dict[str, Any]:
        self.broken.close()
        payload = self.inner.implement(contract_yaml, secrets, side)
        if self.runs == 0:
            self.broken.enter_context(self.workspace.mutated(*WEBHOOK_ROUTE))
        self.runs += 1
        return payload


def assert_reference_session(outcome: SessionOutcome) -> None:
    assert outcome.errors == []
    assert outcome.state == "CERTIFIED"
    assert outcome.matches_reference
    assert outcome.contract["version"] == 9
    assert outcome.contract["status"] == "signed"
    assert outcome.certificate["audit"]["ok"] is True
    assert outcome.certificate["metrics"]["code_lines_shared"] == 0
    assert outcome.certificate["metrics"]["blocked_count"] == 0
    assert outcome.certificate["gateway"]["refused"] == 0
    types = [event["type"] for event in outcome.events]
    assert types.count("turn.accepted") == 7
    assert types.count("question.answered") == 2
    assert types[-1] == "certificate.issued"
    assert states_of(outcome) == [
        "BRIEFING",
        "NEGOTIATING",
        "AWAITING_SIGNATURES",
        "IMPLEMENTING",
        "E2E",
        "CERTIFIED",
    ]


def states_of(outcome: SessionOutcome) -> list[str]:
    return [
        event["payload"]["to"]
        for event in outcome.events
        if event["type"] == "session.state_changed"
    ]


@pytest.mark.golden
def test_scripted_session_with_real_tests_and_apps(tmp_path: Path) -> None:
    workspace = build_workspace(tmp_path / "workspace")
    try:
        outcome = run_full_session(workspace)
    finally:
        workspace.remove()

    assert_reference_session(outcome)
    e2e = outcome.certificate["results"]["e2e"]["results"]
    assert [scenario["id"] for scenario in e2e["scenarios"]] == ["E1", "E2"]
    assert outcome.certificate["results"]["consumer"]["summary"]["passed"] == 9
    assert outcome.certificate["results"]["provider"]["summary"]["passed"] == 4


@pytest.mark.golden
def test_broken_webhook_handler_fails_e2e_and_returns_to_implementing(tmp_path: Path) -> None:
    workspace = build_workspace(tmp_path / "workspace")
    try:
        outcome = run_session(
            LocalProcessE2E({"spokes": workspace.spokes, "parcelly": workspace.parcelly}),
            {
                "spokes": WebhookOffAfterFirstTests(workspace),
                "parcelly": ContractTestsInWorkspace(workspace.parcelly),
            },
        )
    finally:
        workspace.remove()

    assert outcome.errors == []
    assert states_of(outcome)[-4:] == ["E2E", "IMPLEMENTING", "E2E", "CERTIFIED"]
    first = next(event for event in outcome.events if event["type"] == "e2e.result")
    statuses = {item["id"]: item["status"] for item in first["payload"]["report"]["scenarios"]}
    assert first["payload"]["passed"] is False
    assert statuses == {"E1": "failed", "E2": "passed"}
    assert outcome.certificate["certified"] is True


@pytest.mark.golden
def test_session_with_local_sandboxes_and_gateway_e2e() -> None:
    outcome = run_sandbox_session("local", "gateway")

    assert_reference_session(outcome)
    gateway = [event["payload"] for event in outcome.events if event["type"] == "gateway.request"]
    assert {event["party"] for event in gateway} == {"spokes", "parcelly"}
    assert all(event["allowed"] for event in gateway)
    assert all(event["violations"] == [] for event in gateway)
    e2e = outcome.certificate["results"]["e2e"]["results"]
    assert [scenario["status"] for scenario in e2e["scenarios"]] == ["passed", "passed"]


@pytest.mark.sandbox
def test_session_with_token_factory_sandboxes() -> None:
    outcome = run_sandbox_session("tokenfactory", "cleanroom")

    assert_reference_session(outcome)
