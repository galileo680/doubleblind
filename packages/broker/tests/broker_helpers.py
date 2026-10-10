from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from scripted_party import reference_binding, reference_contract, round_one, round_two

from doubleblind_broker.app import create_app
from doubleblind_broker.contracts import load_yaml
from doubleblind_broker.db import build_engine
from doubleblind_broker.e2e import E2EExecutor
from doubleblind_broker.service import BrokerService, BrokerSettings

REPO_ROOT = Path(__file__).resolve().parents[3]
PRESETS_DIR = REPO_ROOT / "demo" / "presets"


@dataclass
class Broker:
    client: TestClient
    service: BrokerService
    session_id: str
    tokens: dict[str, str]

    def headers(self, who: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.tokens[who]}"}

    def url(self, path: str = "") -> str:
        return f"/api/v1/sessions/{self.session_id}{path}"

    def get(self, path: str, who: str = "observer", **params: Any) -> Any:
        response = self.client.get(self.url(path), headers=self.headers(who), params=params)
        assert response.status_code < 400, response.text
        if "json" in response.headers.get("content-type", ""):
            return response.json()
        return response.text

    def post(self, path: str, who: str, body: Any = None, status: int | None = None) -> Any:
        response = self.client.post(self.url(path), headers=self.headers(who), json=body)
        if status is not None:
            assert response.status_code == status, response.text
        return response

    def state(self) -> str:
        return str(self.get("")["state"])

    def turn(
        self,
        who: str,
        patches: list[dict[str, Any]] | None = None,
        status: str = "continue",
        message: str = "Proposal.",
        questions: list[dict[str, Any]] | None = None,
        expected: int = 201,
    ) -> Any:
        session = self.get("")
        body = {
            "round": session["round"] + 1,
            "based_on_version": session["current_contract_version"],
            "message": message,
            "patches": patches or [],
            "questions_for_human": questions or [],
            "status": status,
        }
        return self.post("/turns", who, body, expected)


@contextmanager
def broker(
    autopilot: bool = False,
    e2e_executor: E2EExecutor | None = None,
    max_rounds: int = 14,
    admin_token: str | None = None,
) -> Iterator[Broker]:
    settings = BrokerSettings(
        presets_dir=PRESETS_DIR,
        autopilot=autopilot,
        autopilot_delay_s=0.0,
        next_action_wait_s=0.0,
        max_rounds=max_rounds,
        admin_token=admin_token,
    )
    service = BrokerService(build_engine("sqlite://"), settings, e2e_executor)
    with TestClient(create_app(service=service)) as client:
        headers = {"Authorization": f"Bearer {admin_token}"} if admin_token else {}
        created = client.post(
            "/api/v1/sessions", json={"name": "test", "preset": "spokes-parcelly"}, headers=headers
        )
        assert created.status_code == 201, created.text
        data = created.json()
        yield Broker(client, service, data["session_id"], data["tokens"])


def briefed(session: Broker) -> Broker:
    session.post("/start", "observer", status=200)
    session.post("/briefs", "agent_spokes", {"summary": "Bike shop."}, 201)
    session.post("/briefs", "agent_parcelly", {"summary": "Courier."}, 201)
    return session


REFERENCE = reference_contract()


def opening(session: Broker) -> Any:
    return session.turn("agent_parcelly", round_one(load_yaml(session.get("/contract")), REFERENCE))


def converge(session: Broker) -> None:
    opening(session)
    session.turn("agent_spokes", round_two(load_yaml(session.get("/contract")), REFERENCE))
    session.turn("agent_parcelly", status="ready_to_sign")
    session.turn("agent_spokes", status="ready_to_sign")


def signed(session: Broker) -> dict[str, Any]:
    briefed(session)
    converge(session)
    session.post("/signatures", "human_spokes", {"version": 3}, 201)
    session.post("/signatures", "human_parcelly", {"version": 3}, 201)
    return load_yaml(session.get("/contract"))


def run_payload(
    contract: dict[str, Any], side: str, passed: bool = True, binding: bool = True
) -> dict[str, Any]:
    ids = [item["id"] for item in contract["scenarios"] if item["side"] == side]
    party = "spokes" if side == "consumer" else "parcelly"
    payload: dict[str, Any] = {
        "side": side,
        "contract_version": contract["version"],
        "summary": {"passed": len(ids) if passed else 0},
        "results": [
            {"scenario_id": scenario_id, "status": "passed" if passed else "failed"}
            for scenario_id in ids
        ],
    }
    if binding:
        payload["binding_yaml"] = reference_binding(party)
    return payload
