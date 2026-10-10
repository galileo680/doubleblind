import pytest

from doubleblind_broker.state_machine import (
    TRANSITIONS,
    Effect,
    InvalidTransitionError,
    State,
    Trigger,
    transition,
)

EXPECTED = [
    (State.CREATED, Trigger.START, State.BRIEFING, (Effect.REQUEST_BRIEFS,)),
    (State.BRIEFING, Trigger.BRIEFS_COMPLETE, State.NEGOTIATING, (Effect.PROVIDER_TURN,)),
    (State.NEGOTIATING, Trigger.CONVERGED, State.AWAITING_SIGNATURES, (Effect.REQUEST_SIGNATURES,)),
    (State.NEGOTIATING, Trigger.ROUNDS_EXHAUSTED, State.ESCALATED, (Effect.NOTIFY_HUMANS,)),
    (State.ESCALATED, Trigger.HUMAN_ACTION, State.NEGOTIATING, ()),
    (
        State.AWAITING_SIGNATURES,
        Trigger.BOTH_SIGNED,
        State.IMPLEMENTING,
        (Effect.MARK_SIGNED, Effect.GENERATE_SECRETS, Effect.REQUEST_IMPLEMENTATION),
    ),
    (State.IMPLEMENTING, Trigger.BOTH_READY, State.E2E, (Effect.RUN_E2E,)),
    (State.IMPLEMENTING, Trigger.CONTRACT_GAP, State.AMENDING, ()),
    (State.AMENDING, Trigger.CONVERGED, State.AWAITING_SIGNATURES, (Effect.REQUEST_SIGNATURES,)),
    (State.AMENDING, Trigger.ROUNDS_EXHAUSTED, State.ESCALATED, (Effect.NOTIFY_HUMANS,)),
    (State.E2E, Trigger.E2E_PASSED, State.CERTIFIED, (Effect.ISSUE_CERTIFICATE,)),
    (State.E2E, Trigger.E2E_FAILED, State.IMPLEMENTING, (Effect.SHARE_E2E_REPORT,)),
]


@pytest.mark.parametrize(("source", "trigger", "target", "effects"), EXPECTED)
def test_every_transition_from_the_specification(
    source: State, trigger: Trigger, target: State, effects: tuple[Effect, ...]
) -> None:
    assert transition(source, trigger) == (target, effects)


def test_the_table_has_no_extra_transitions() -> None:
    assert len(TRANSITIONS) == len(EXPECTED)


@pytest.mark.parametrize(
    "state", [state for state in State if state not in (State.CERTIFIED, State.FAILED)]
)
def test_any_running_session_can_fail(state: State) -> None:
    assert transition(state, Trigger.FAILURE) == (State.FAILED, ())


@pytest.mark.parametrize("state", [State.CERTIFIED, State.FAILED])
def test_finished_sessions_cannot_change(state: State) -> None:
    with pytest.raises(InvalidTransitionError):
        transition(state, Trigger.FAILURE)


def test_unknown_transitions_are_rejected() -> None:
    with pytest.raises(
        InvalidTransitionError, match="cannot apply 'both_signed' in state BRIEFING"
    ):
        transition(State.BRIEFING, Trigger.BOTH_SIGNED)
