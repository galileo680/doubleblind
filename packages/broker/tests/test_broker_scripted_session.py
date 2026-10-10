from pathlib import Path

import pytest
from demo_workspace import build_workspace
from scripted_session import SessionOutcome, run_full_session, run_quick_session


def assert_reference_session(outcome: SessionOutcome) -> None:
    assert outcome.errors == []
    assert outcome.state == "CERTIFIED"
    assert outcome.matches_reference
    assert outcome.contract["version"] == 9
    assert outcome.contract["status"] == "signed"
    assert outcome.certificate["audit"]["ok"] is True
    assert outcome.certificate["metrics"]["code_lines_shared"] == 0
    assert outcome.certificate["metrics"]["blocked_count"] == 0
    types = [event["type"] for event in outcome.events]
    assert types.count("turn.accepted") == 7
    assert types.count("question.answered") == 2
    assert types[-1] == "certificate.issued"
    states = [
        event["payload"]["to"]
        for event in outcome.events
        if event["type"] == "session.state_changed"
    ]
    assert states == [
        "BRIEFING",
        "NEGOTIATING",
        "AWAITING_SIGNATURES",
        "IMPLEMENTING",
        "E2E",
        "CERTIFIED",
    ]


def test_scripted_parties_negotiate_the_reference_contract() -> None:
    outcome = run_quick_session()

    assert_reference_session(outcome)


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
