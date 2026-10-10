from enum import StrEnum


class State(StrEnum):
    CREATED = "CREATED"
    BRIEFING = "BRIEFING"
    NEGOTIATING = "NEGOTIATING"
    ESCALATED = "ESCALATED"
    AWAITING_SIGNATURES = "AWAITING_SIGNATURES"
    IMPLEMENTING = "IMPLEMENTING"
    AMENDING = "AMENDING"
    E2E = "E2E"
    CERTIFIED = "CERTIFIED"
    FAILED = "FAILED"


class Trigger(StrEnum):
    START = "start"
    BRIEFS_COMPLETE = "briefs_complete"
    CONVERGED = "converged"
    ROUNDS_EXHAUSTED = "rounds_exhausted"
    HUMAN_ACTION = "human_action"
    BOTH_SIGNED = "both_signed"
    BOTH_READY = "both_ready"
    CONTRACT_GAP = "contract_gap"
    E2E_PASSED = "e2e_passed"
    E2E_FAILED = "e2e_failed"
    FAILURE = "failure"


class Effect(StrEnum):
    REQUEST_BRIEFS = "request_briefs"
    PROVIDER_TURN = "provider_turn"
    REQUEST_SIGNATURES = "request_signatures"
    NOTIFY_HUMANS = "notify_humans"
    MARK_SIGNED = "mark_signed"
    GENERATE_SECRETS = "generate_secrets"
    REQUEST_IMPLEMENTATION = "request_implementation"
    REPORTER_TURN = "reporter_turn"
    RUN_E2E = "run_e2e"
    ISSUE_CERTIFICATE = "issue_certificate"
    SHARE_E2E_REPORT = "share_e2e_report"


TERMINAL_STATES = frozenset({State.CERTIFIED, State.FAILED})
NEGOTIATION_STATES = frozenset({State.NEGOTIATING, State.AMENDING})

TRANSITIONS: dict[tuple[State, Trigger], tuple[State, tuple[Effect, ...]]] = {
    (State.CREATED, Trigger.START): (State.BRIEFING, (Effect.REQUEST_BRIEFS,)),
    (State.BRIEFING, Trigger.BRIEFS_COMPLETE): (State.NEGOTIATING, (Effect.PROVIDER_TURN,)),
    (State.NEGOTIATING, Trigger.CONVERGED): (
        State.AWAITING_SIGNATURES,
        (Effect.REQUEST_SIGNATURES,),
    ),
    (State.NEGOTIATING, Trigger.ROUNDS_EXHAUSTED): (State.ESCALATED, (Effect.NOTIFY_HUMANS,)),
    (State.ESCALATED, Trigger.HUMAN_ACTION): (State.NEGOTIATING, ()),
    (State.AWAITING_SIGNATURES, Trigger.BOTH_SIGNED): (
        State.IMPLEMENTING,
        (Effect.MARK_SIGNED, Effect.GENERATE_SECRETS, Effect.REQUEST_IMPLEMENTATION),
    ),
    (State.IMPLEMENTING, Trigger.BOTH_READY): (State.E2E, (Effect.RUN_E2E,)),
    (State.IMPLEMENTING, Trigger.CONTRACT_GAP): (State.AMENDING, (Effect.REPORTER_TURN,)),
    (State.AMENDING, Trigger.CONVERGED): (State.AWAITING_SIGNATURES, (Effect.REQUEST_SIGNATURES,)),
    (State.AMENDING, Trigger.ROUNDS_EXHAUSTED): (State.ESCALATED, (Effect.NOTIFY_HUMANS,)),
    (State.E2E, Trigger.E2E_PASSED): (State.CERTIFIED, (Effect.ISSUE_CERTIFICATE,)),
    (State.E2E, Trigger.E2E_FAILED): (State.IMPLEMENTING, (Effect.SHARE_E2E_REPORT,)),
}


class InvalidTransitionError(Exception):
    def __init__(self, state: State, trigger: Trigger) -> None:
        super().__init__(f"cannot apply '{trigger}' in state {state}")
        self.state = state
        self.trigger = trigger


def transition(state: State, trigger: Trigger) -> tuple[State, tuple[Effect, ...]]:
    if trigger is Trigger.FAILURE and state not in TERMINAL_STATES:
        return State.FAILED, ()
    try:
        return TRANSITIONS[(state, trigger)]
    except KeyError as error:
        raise InvalidTransitionError(state, trigger) from error


def allowed_triggers(state: State) -> list[Trigger]:
    triggers = [trigger for (source, trigger) in TRANSITIONS if source is state]
    if state not in TERMINAL_STATES:
        triggers.append(Trigger.FAILURE)
    return triggers
