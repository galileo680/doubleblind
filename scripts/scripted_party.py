import copy
import os
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import httpx
from demo_workspace import BINDING_FILE, REFERENCE_CONTRACT, REFERENCE_DIR, side_env

from doubleblind_contract.loader import load_contract_text, parse_yaml
from doubleblind_contract.models import Side
from doubleblind_contract.runner_consumer import run_consumer_tests
from doubleblind_contract.runner_provider import run_provider_tests
from doubleblind_contract.runtime import RunReport, secret_env_name

Contract = dict[str, Any]
PatchBuilder = Callable[[Contract, Contract], list[dict[str, Any]]]
MAX_CONFLICT_RETRIES = 8
REASON_LENGTH = 200


def reference_contract() -> Contract:
    return parse_yaml(REFERENCE_CONTRACT.read_text(encoding="utf-8"))


def decision(reference: Contract, decision_id: str) -> dict[str, Any]:
    found = next(item for item in reference["decisions"] if item["id"] == decision_id)
    return {key: value for key, value in found.items() if key != "approved_by"}


def scenario(reference: Contract, scenario_id: str) -> dict[str, Any]:
    return copy.deepcopy(next(item for item in reference["scenarios"] if item["id"] == scenario_id))


def add_decisions(current: Contract, reference: Contract, *ids: str) -> list[dict[str, Any]]:
    values = [decision(reference, decision_id) for decision_id in ids]
    if "decisions" not in current:
        return [{"op": "add", "path": "/decisions", "value": values}]
    return [{"op": "add", "path": "/decisions/-", "value": value} for value in values]


def add_scenarios(reference: Contract, *ids: str) -> list[dict[str, Any]]:
    return [
        {"op": "add", "path": "/scenarios/-", "value": scenario(reference, scenario_id)}
        for scenario_id in ids
    ]


def adopt_human_decision(
    current: Contract, reference: Contract, human_id: str, decision_id: str
) -> list[dict[str, Any]]:
    index = next(
        position for position, item in enumerate(current["decisions"]) if item["id"] == human_id
    )
    return [
        {"op": "replace", "path": f"/decisions/{index}/id", "value": decision_id},
        {
            "op": "replace",
            "path": f"/decisions/{index}/text",
            "value": decision(reference, decision_id)["text"],
        },
    ]


def round_one(current: Contract, reference: Contract) -> list[dict[str, Any]]:
    return [
        {"op": "replace", "path": "/environments", "value": reference["environments"]},
        {"op": "add", "path": "/secrets", "value": reference["secrets"]},
        {"op": "add", "path": "/glossary", "value": reference["glossary"]},
        {"op": "add", "path": "/schemas", "value": reference["schemas"]},
        {"op": "replace", "path": "/operations", "value": reference["operations"]},
        {"op": "add", "path": "/webhooks", "value": reference["webhooks"]},
        *add_scenarios(reference, "P2", "P4"),
    ]


def round_two(current: Contract, reference: Contract) -> list[dict[str, Any]]:
    return [
        *add_decisions(current, reference, "D1", "D3", "D7"),
        *add_scenarios(reference, "S1", "S5"),
    ]


def round_three(current: Contract, reference: Contract) -> list[dict[str, Any]]:
    return [*add_decisions(current, reference, "D4"), *add_scenarios(reference, "P1", "P3")]


def round_four(current: Contract, reference: Contract) -> list[dict[str, Any]]:
    return [*add_decisions(current, reference, "D2"), *add_scenarios(reference, "S2", "S3")]


def round_five(current: Contract, reference: Contract) -> list[dict[str, Any]]:
    return [
        *add_decisions(current, reference, "D8"),
        {"op": "add", "path": "/semantic_mappings", "value": reference["semantic_mappings"]},
        *add_scenarios(reference, "S7", "S8", "S9"),
    ]


def round_six(current: Contract, reference: Contract) -> list[dict[str, Any]]:
    return [
        *adopt_human_decision(current, reference, "H1", "D5"),
        *adopt_human_decision(current, reference, "H2", "D6"),
        *add_scenarios(reference, "S4", "S6", "E1", "E2"),
    ]


def no_patches(current: Contract, reference: Contract) -> list[dict[str, Any]]:
    return []


@dataclass(frozen=True)
class PlannedTurn:
    message: str
    patches: PatchBuilder
    questions: list[dict[str, Any]] = field(default_factory=list)
    status: str = "continue"
    wait_for_answers: bool = False


PLAN: Mapping[str, list[PlannedTurn]] = {
    "parcelly": [
        PlannedTurn(
            "Proponuję szkielet: create_shipment i webhook shipment_status_changed "
            "z podpisem HMAC. Waga w gramach, gabaryt A/B/C według wagi, przesyłki "
            "powyżej 25 kg tylko przez create_pallet.",
            round_one,
        ),
        PlannedTurn(
            "Bez Idempotency-Key powstają duplikaty i podwójne opłaty. Proponuję wymagać klucza "
            "dla Spokes (D4); to zmiana po naszej stronie.",
            round_three,
        ),
        PlannedTurn(
            "Dodaję wymóg odpowiedzi 401 dla błędnego podpisu, mapowanie statusów i scenariusze "
            "webhooków.",
            round_five,
        ),
        PlannedTurn("Gotowi do podpisu na tej wersji.", no_patches, status="ready_to_sign"),
    ],
    "spokes": [
        PlannedTurn(
            "Nasza waga jest w kilogramach, adres w jednym polu, a telefon w dowolnym formacie. "
            "Przejmujemy konwersje (D1, D3, D7).",
            round_two,
            questions=[
                {
                    "id": "tmp-1",
                    "text": "Co zrobić z adresami nieparsowalnymi, których nie da się rozbić "
                    "na pola?",
                    "options": [
                        "tak, oznacz do ręcznej weryfikacji i nie wysyłaj",
                        "wyślij bez rozbicia",
                    ],
                    "default": "tak, oznacz do ręcznej weryfikacji i nie wysyłaj",
                }
            ],
        ),
        PlannedTurn(
            "Akceptujemy D4, kluczem będzie reference. Mamy rowery cargo ważące 27 kg (D2, S2).",
            round_four,
            questions=[
                {
                    "id": "tmp-2",
                    "text": "Czy dodać status zamówienia returned dla zwrotów?",
                    "options": ["tak", "nie, mapuj na cancelled"],
                    "default": "tak",
                }
            ],
        ),
        PlannedTurn(
            "Doprecyzowuję decyzje z odpowiedzi ludzi i dodaję S4, S6 oraz scenariusze E2E. "
            "Gotowi do podpisu.",
            round_six,
            status="ready_to_sign",
            wait_for_answers=True,
        ),
    ],
}

BRIEFS: Mapping[str, dict[str, Any]] = {
    "spokes": {
        "summary": "Sklep rowerowy, który po opłaceniu zamówienia chce automatycznie zamawiać "
        "przesyłkę u kuriera.",
        "data_we_have": [
            "waga w kilogramach",
            "adres w jednym polu",
            "telefon w dowolnym formacie",
        ],
        "needs": ["utworzenie przesyłki po płatności", "powiadomienia o zmianie statusu"],
    },
    "parcelly": {
        "summary": "Firma kurierska z API przesyłek i palet oraz podpisanymi webhookami.",
        "data_we_need": ["waga w gramach", "adres w polach", "telefon +48XXXXXXXXX"],
        "rules": ["limit 25 kg dla przesyłek", "gabaryt zależy od wagi"],
    },
}


class Implementer(Protocol):
    def implement(
        self, contract_yaml: str, secrets: Mapping[str, str], side: Side
    ) -> dict[str, Any]: ...


def reference_binding(party_key: str) -> str:
    return (REFERENCE_DIR / party_key / BINDING_FILE).read_text(encoding="utf-8")


class ReportedGreen:
    def __init__(self, party_key: str) -> None:
        self.party_key = party_key

    def implement(
        self, contract_yaml: str, secrets: Mapping[str, str], side: Side
    ) -> dict[str, Any]:
        contract = load_contract_text(contract_yaml)
        results = [
            {"scenario_id": item.id, "status": "passed"} for item in contract.scenarios_for(side)
        ]
        return {
            "summary": {"passed": len(results), "failed": 0, "error": 0},
            "results": results,
            "binding_yaml": reference_binding(self.party_key),
        }


class ContractTestsInWorkspace:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace

    def implement(
        self, contract_yaml: str, secrets: Mapping[str, str], side: Side
    ) -> dict[str, Any]:
        env = {**os.environ, **side_env(side)}
        env.update({secret_env_name(name): value for name, value in secrets.items()})
        binding = self.workspace / BINDING_FILE
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            contract_path = Path(directory) / "contract.yaml"
            contract_path.write_text(contract_yaml, encoding="utf-8")
            runner = run_consumer_tests if side == "consumer" else run_provider_tests
            report = runner(contract_path, binding, env=env)
        return test_run_payload(report, binding.read_text(encoding="utf-8"))


def test_run_payload(report: RunReport, binding_yaml: str) -> dict[str, Any]:
    return payload_from_report(report.to_dict(include_logs=False), binding_yaml)


def payload_from_report(report: Mapping[str, Any], binding_yaml: str) -> dict[str, Any]:
    results = []
    for item in report["scenarios"]:
        result: dict[str, Any] = {"scenario_id": item["id"], "status": item["status"]}
        if item["failures"]:
            result["sanitized_reason"] = item["failures"][0]["message"][:REASON_LENGTH]
        results.append(result)
    return {"summary": report["summary"], "results": results, "binding_yaml": binding_yaml}


class ScriptedParty:
    def __init__(
        self,
        client: httpx.Client,
        session_id: str,
        party_key: str,
        implementer: Implementer,
        poll_s: float = 5.0,
        stop: threading.Event | None = None,
    ) -> None:
        self.client = client
        self.session_id = session_id
        self.party_key = party_key
        self.implementer = implementer
        self.poll_s = poll_s
        self.stop = stop or threading.Event()
        self.plan = list(PLAN[party_key])
        self.reference = reference_contract()
        self.log: list[str] = []

    def url(self, path: str) -> str:
        return f"/api/v1/sessions/{self.session_id}{path}"

    def get(self, path: str, **params: Any) -> Any:
        response = self.client.get(self.url(path), params=params)
        response.raise_for_status()
        return (
            response.json() if "json" in response.headers.get("content-type", "") else response.text
        )

    def run(self, timeout_s: float = 600.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if self.stop.is_set():
                self.log.append("stopped because the other party failed")
                return
            action = self.get("/next-action", wait=self.poll_s)
            kind, context = action["action"], action["context"]
            if kind == "done":
                self.log.append(f"done in state {context.get('state')}")
                return
            if kind == "build_psm_and_brief":
                self.post("/briefs", BRIEFS[self.party_key])
            elif kind in ("negotiate", "amend"):
                self.take_turn()
            elif kind in ("implement", "fix_after_e2e"):
                self.implement(context)
        raise TimeoutError(f"{self.party_key} did not finish within {timeout_s:.0f} s")

    def post(self, path: str, body: Any) -> httpx.Response:
        response = self.client.post(self.url(path), json=body)
        if response.status_code >= 400:
            raise RuntimeError(
                f"{self.party_key} POST {path}: {response.status_code} {response.text}"
            )
        return response

    def take_turn(self) -> None:
        planned = (
            self.plan.pop(0)
            if self.plan
            else PlannedTurn("Gotowi do podpisu.", no_patches, status="ready_to_sign")
        )
        if planned.wait_for_answers:
            self.wait_for_answers()
        for _ in range(MAX_CONFLICT_RETRIES):
            session = self.get("")
            current = parse_yaml(self.get("/contract"))
            turn = {
                "round": session["round"] + 1,
                "based_on_version": session["current_contract_version"],
                "message": planned.message,
                "patches": planned.patches(current, self.reference),
                "questions_for_human": planned.questions,
                "status": planned.status,
            }
            response = self.client.post(self.url("/turns"), json=turn)
            if response.status_code == 409:
                continue
            if response.status_code >= 400:
                raise RuntimeError(f"{self.party_key} turn rejected: {response.text}")
            self.log.append(f"round {turn['round']} -> v{response.json()['contract_version']}")
            return
        raise RuntimeError(f"{self.party_key} kept losing the race for the contract version")

    def wait_for_answers(self, timeout_s: float = 60.0) -> None:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            questions = self.get("/questions")
            if all(question["answer"] is not None for question in questions):
                return
            time.sleep(0.1)
        raise TimeoutError(f"{self.party_key}: questions were not answered in time")

    def implement(self, context: Mapping[str, Any]) -> None:
        version = context["signed_version"]
        contract_yaml = self.get("/contract", version=version)
        payload = self.implementer.implement(contract_yaml, context["secrets"], context["role"])
        self.post(
            "/test-runs",
            {"side": context["role"], "contract_version": version, **payload},
        )
        self.log.append(f"tests on v{version}: {payload['summary']}")
