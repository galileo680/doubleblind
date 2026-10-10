from broker_helpers import briefed, broker, converge, opening
from scripted_party import BRIEFS, reference_contract, round_three, round_two

from doubleblind_broker.contracts import load_yaml

REFERENCE = reference_contract()


def test_session_creation_returns_five_tokens() -> None:
    with broker() as session:
        assert set(session.tokens) == {
            "observer",
            "agent_spokes",
            "agent_parcelly",
            "human_spokes",
            "human_parcelly",
        }
        view = session.get("")
        assert view["state"] == "CREATED"
        assert view["current_contract_version"] == 1
        assert [party["party"] for party in view["parties"]] == ["spokes", "parcelly"]
        skeleton = load_yaml(session.get("/contract"))
        assert skeleton["id"] == "spokes-parcelly-shipping"
        assert skeleton["parties"]["provider"]["name"] == "Parcelly (courier)"


def test_tokens_are_checked() -> None:
    with broker() as session:
        missing = session.client.get(session.url())
        unknown = session.client.get(session.url(), headers={"Authorization": "Bearer nope"})
        agent_starts = session.post("/start", "agent_spokes")

    assert missing.status_code == 401
    assert unknown.status_code == 401
    assert agent_starts.status_code == 403


def test_admin_token_protects_session_creation() -> None:
    with broker(admin_token="secret-admin") as session:
        anonymous = session.client.post(
            "/api/v1/sessions", json={"name": "x", "preset": "spokes-parcelly"}
        )
        unknown_preset = session.client.post(
            "/api/v1/sessions",
            json={"name": "x", "preset": "nothing"},
            headers={"Authorization": "Bearer secret-admin"},
        )

    assert anonymous.status_code == 401
    assert unknown_preset.status_code == 400


def test_next_action_drives_both_parties_through_briefing() -> None:
    with broker() as session:
        assert session.get("/next-action", "agent_spokes")["action"] == "wait"
        session.post("/start", "observer", status=200)
        assert session.get("/next-action", "agent_spokes")["action"] == "build_psm_and_brief"
        session.post("/briefs", "agent_spokes", BRIEFS["spokes"], 201)
        assert session.get("/next-action", "agent_spokes")["action"] == "wait"
        assert session.state() == "BRIEFING"
        session.post("/briefs", "agent_parcelly", BRIEFS["parcelly"], 201)

        assert session.state() == "NEGOTIATING"
        assert session.get("/next-action", "agent_parcelly") == {
            "action": "negotiate",
            "context": {"role": "provider", "round": 1, "based_on_version": 1},
        }
        assert session.get("/briefs/counterparty", "agent_spokes")["party"] == "parcelly"
        assert session.post("/briefs", "agent_spokes", {"summary": "again"}).status_code == 409


def test_brief_with_code_is_blocked() -> None:
    with broker() as session:
        session.post("/start", "observer", status=200)
        response = session.post(
            "/briefs",
            "agent_spokes",
            {"summary": "def handler(order):\n    return order\nimport os"},
        )

        assert response.status_code == 422
        assert response.json()["code"] == "EGRESS_BLOCKED"
        assert session.get("")["metrics"]["blocked_count"] == 1


def test_turn_order_round_and_version_are_enforced() -> None:
    with broker() as session:
        briefed(session)
        not_spokes_turn = session.turn("agent_spokes", expected=409)
        stale = session.post(
            "/turns",
            "agent_parcelly",
            {
                "round": 1,
                "based_on_version": 7,
                "message": "x",
                "patches": [],
                "status": "continue",
            },
        )
        wrong_round = session.post(
            "/turns",
            "agent_parcelly",
            {
                "round": 3,
                "based_on_version": 1,
                "message": "x",
                "patches": [],
                "status": "continue",
            },
        )

    assert not_spokes_turn.json()["code"] == "NOT_YOUR_TURN"
    assert stale.status_code == 409
    assert stale.json()["details"] == {"current_version": 1}
    assert wrong_round.json()["code"] == "WRONG_ROUND"


def test_accepted_turn_creates_a_version_and_passes_the_turn() -> None:
    with broker() as session:
        briefed(session)
        result = opening(session).json()
        view = session.get("")
        contract = load_yaml(session.get("/contract"))
        diff = session.get("/contract/diff", **{"from": 1, "to": 2})
        turns = session.get("/turns")

    assert result == {"accepted": True, "round": 1, "contract_version": 2, "questions": []}
    assert view["turn_party"] == "spokes"
    assert contract["version"] == 2
    assert contract["status"] == "proposed"
    assert set(contract["operations"]) == {"create_shipment", "create_pallet"}
    assert "+  create_shipment:" in diff
    assert turns[0]["party"] == "parcelly"


def test_turn_with_code_is_rejected_and_hidden_from_the_other_party() -> None:
    with broker() as session:
        briefed(session)
        response = session.turn(
            "agent_parcelly",
            message="Our handler:\ndef create(shipment):\n    return save(shipment)\nimport db",
            expected=422,
        )
        own = session.get("/turns", "agent_parcelly")
        theirs = session.get("/turns", "agent_spokes")
        spokes_events = session.get("/events", "agent_spokes")

    assert response.json()["code"] == "EGRESS_BLOCKED"
    assert own[0]["accepted"] is False
    assert theirs == []
    assert "turn.rejected" not in {event["type"] for event in spokes_events}


def test_patches_that_break_the_contract_are_rejected() -> None:
    with broker() as session:
        briefed(session)
        protected = session.turn(
            "agent_parcelly", [{"op": "replace", "path": "/version", "value": 99}], expected=422
        )
        invalid = session.turn(
            "agent_parcelly",
            [{"op": "add", "path": "/scenarios/-", "value": {"id": "S1"}}],
            expected=422,
        )
        missing = session.turn(
            "agent_parcelly", [{"op": "remove", "path": "/nothing"}], expected=422
        )
        view = session.get("")

    assert "managed by the broker" in protected.json()["details"][0]
    assert invalid.json()["code"] == "INVALID_CONTRACT"
    assert "cannot be applied" in missing.json()["details"][0]
    assert view["current_contract_version"] == 1
    assert view["round"] == 0


def test_questions_become_decisions_after_an_answer() -> None:
    with broker() as session:
        briefed(session)
        opening(session)
        current = load_yaml(session.get("/contract"))
        result = session.turn(
            "agent_spokes",
            round_two(current, REFERENCE),
            questions=[
                {"text": "Czy dodać status returned?", "options": ["tak", "nie"], "default": "tak"}
            ],
        ).json()
        hidden = session.get("/questions", "agent_parcelly")
        wrong_human = session.post("/questions/Q1/answer", "human_parcelly", {"answer": "nie"})
        answered = session.client.post(
            "/api/v1/questions/Q1/answer",
            headers=session.headers("human_spokes"),
            json={"answer": "tak"},
        )
        again = session.post("/questions/Q1/answer", "human_spokes", {"answer": "tak"})
        contract = load_yaml(session.get("/contract"))
        questions = session.get("/questions", "human_spokes")

    assert result["questions"] == ["Q1"]
    assert hidden == []
    assert wrong_human.status_code == 403
    assert answered.json() == {"qid": "Q1", "decision_id": "H1"}
    assert again.status_code == 409
    assert contract["version"] == 4
    assert contract["decisions"][-1] == {
        "id": "H1",
        "text": "Czy dodać status returned? Odpowiedź: tak",
        "owner": "consumer",
        "origin_question": "Q1",
    }
    assert questions[0]["answered_by"] == "human:spokes"


def test_autopilot_answers_from_the_preset() -> None:
    with broker(autopilot=True) as session:
        briefed(session)
        opening(session)
        session.turn(
            "agent_spokes",
            round_two(load_yaml(session.get("/contract")), REFERENCE),
            questions=[{"text": "Co z adresami nieparsowalnymi?", "options": ["a", "b"]}],
        )
        session.client.portal.call(session.service.wait_for_background)
        question = session.get("/questions", "human_spokes")[0]

    assert question["answer"] == "tak, oznacz do ręcznej weryfikacji i nie wysyłaj"


def test_both_sides_ready_on_the_same_version_converge() -> None:
    with broker() as session:
        briefed(session)
        opening(session)
        session.turn(
            "agent_spokes",
            round_two(load_yaml(session.get("/contract")), REFERENCE),
            status="ready_to_sign",
        )
        assert session.state() == "NEGOTIATING"
        session.turn("agent_parcelly", status="ready_to_sign")

        assert session.state() == "AWAITING_SIGNATURES"


def test_ready_with_new_patches_does_not_converge() -> None:
    with broker() as session:
        briefed(session)
        opening(session)
        session.turn("agent_spokes", status="ready_to_sign")
        session.turn(
            "agent_parcelly",
            round_three(load_yaml(session.get("/contract")), REFERENCE),
            status="ready_to_sign",
        )

        assert session.state() == "NEGOTIATING"


def test_open_questions_block_convergence() -> None:
    with broker() as session:
        briefed(session)
        opening(session)
        session.turn(
            "agent_spokes",
            status="ready_to_sign",
            questions=[{"text": "Czy wysyłać w weekend?", "options": ["tak"]}],
        )
        session.turn("agent_parcelly", status="ready_to_sign")

        assert session.state() == "NEGOTIATING"


def test_too_many_rounds_escalate_and_humans_can_resume() -> None:
    with broker(max_rounds=2) as session:
        briefed(session)
        opening(session)
        session.turn("agent_spokes")
        assert session.state() == "ESCALATED"
        assert session.get("/next-action", "agent_parcelly")["action"] == "wait"
        session.post("/resume", "human_spokes", {"extra_rounds": 2}, 200)

        assert session.state() == "NEGOTIATING"
        assert session.get("")["max_rounds"] == 4


def test_signatures_on_the_current_version_start_implementation() -> None:
    with broker() as session:
        briefed(session)
        converge(session)
        stale = session.post("/signatures", "human_spokes", {"version": 2})
        session.post("/signatures", "human_spokes", {"version": 3}, 201)
        assert session.state() == "AWAITING_SIGNATURES"
        session.post("/signatures", "human_parcelly", {"version": 3}, 201)
        contract = load_yaml(session.get("/contract"))
        action = session.get("/next-action", "agent_spokes")

    assert stale.status_code == 409
    assert contract["status"] == "signed"
    assert {tuple(item["approved_by"]) for item in contract["decisions"]} == {
        ("spokes", "parcelly")
    }
    assert action["action"] == "implement"
    assert action["context"]["signed_version"] == 3
    assert set(action["context"]["secrets"]) == {"partner_api_key", "webhook_secret"}
