import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
from demo_workspace import REPO_ROOT
from eval_negotiation import CONTRACT
from eval_negotiation import evaluate as evaluate_contract
from sandbox_party import PARTIES, SandboxKind, base_heads, sandbox_factory
from scripted_session import PRESET, running_broker

from doubleblind_agent.main import PartyConfig, PartyRuntime
from doubleblind_broker.db import build_engine
from doubleblind_broker.service import BrokerService, BrokerSettings
from doubleblind_contract.loader import parse_yaml
from doubleblind_sandbox.local import LocalSandboxProvider

STATE_ROOT = REPO_ROOT / "volumes" / "state"
CASSETTE = REPO_ROOT / "demo" / "recordings" / "spokes-parcelly.jsonl"
ACTIVE_STATES = {
    "negotiating": {"CREATED", "BRIEFING"},
    "signatures": {"CREATED", "BRIEFING", "NEGOTIATING", "AMENDING"},
}
ACTIONS = {
    "negotiating": frozenset({"build_psm_and_brief"}),
    "signatures": frozenset({"build_psm_and_brief", "negotiate", "amend"}),
}
HIDDEN_RULES = {
    "pallet above 25 kg": r"pallet|palet",
    "size from weight": r"\bsize\b|gabaryt|SIZE_WEIGHT",
    "idempotency": r"idempoten",
    "postcode matches city": r"(?=.*(postcode|postal|kod))(?=.*(city|miast))",
    "phone and postcode format": r"\+48|phone|telefon",
    "signed webhooks": r"signature|hmac|podpis",
}
SPOKES_QUIRKS = {"address": r"address|adres", "phone": r"phone|telefon", "weight": r"weight|wag"}


def found(pattern: str, texts: list[str]) -> bool:
    return any(re.search(pattern, text, re.IGNORECASE | re.DOTALL) for text in texts)


def check(label: str, ok: bool) -> bool:
    print(f"{'OK  ' if ok else 'MISS'} {label}")
    return ok


def load(party: str, name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((STATE_ROOT / party / name).read_text(encoding="utf-8"))
    return data


async def run_parties(
    url: str,
    session_id: str,
    tokens: dict[str, str],
    bases: dict[str, str],
    factory: Any,
    until: str,
) -> list[PartyRuntime]:
    runtimes = [
        PartyRuntime(
            PartyConfig(
                url, tokens[f"agent_{party}"], session_id, party, bases[party], STATE_ROOT / party
            ),
            factory,
            ACTIONS[until],
        )
        for party in PARTIES
    ]
    tasks = [asyncio.create_task(runtime.run()) for runtime in runtimes]
    async with httpx.AsyncClient(
        base_url=url, headers={"Authorization": f"Bearer {tokens['observer']}"}
    ) as viewer:
        while not any(task.done() for task in tasks):
            state = (await viewer.get(f"/api/v1/sessions/{session_id}")).json()["state"]
            if state not in ACTIVE_STATES[until]:
                break
            await asyncio.sleep(1.0)
    for task in tasks:
        task.cancel()
    for result in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
            raise result
    return runtimes


def run_agents(kind: SandboxKind, local_root: Path, until: str) -> dict[str, Any]:
    factory = sandbox_factory(kind, local_root)
    bases = base_heads(kind, local_root)
    settings = BrokerSettings(
        presets_dir=REPO_ROOT / "demo" / "presets",
        autopilot=True,
        autopilot_delay_s=0.0,
        next_action_wait_s=2.0,
    )
    service = BrokerService(build_engine("sqlite://"), settings)
    with running_broker(service) as url, httpx.Client(base_url=url, timeout=60.0) as admin:
        created = admin.post("/api/v1/sessions", json={"name": "agents", "preset": PRESET}).json()
        session_id, tokens = created["session_id"], created["tokens"]
        observer = {"Authorization": f"Bearer {tokens['observer']}"}
        sessions = f"/api/v1/sessions/{session_id}"
        admin.post(f"{sessions}/start", headers=observer).raise_for_status()
        runtimes = asyncio.run(run_parties(url, session_id, tokens, bases, factory, until))
        for runtime in runtimes:
            for line in runtime.log:
                print(f"[{runtime.config.party}] {line}")
        return {
            "session": admin.get(sessions, headers=observer).json(),
            "events": admin.get(f"{sessions}/events", headers=observer).json(),
            "turns": admin.get(f"{sessions}/turns", headers=observer).json(),
            "questions": admin.get(f"{sessions}/questions", headers=observer).json(),
            "contract": admin.get(f"{sessions}/contract", headers=observer).text,
        }


def evaluate_briefing(events: list[dict[str, Any]]) -> bool:
    parcelly, spokes = load("parcelly", "psm.json"), load("spokes", "psm.json")
    hidden = [rule["rule"] for rule in parcelly["business_rules"] if not rule["public"]]
    briefs = {
        event["payload"]["party"]: json.dumps(event["payload"]["brief"], ensure_ascii=False)
        for event in events
        if event["type"] == "brief.submitted"
    }
    hooks = [json.dumps(hook) for hook in spokes["events_and_hooks"]]
    quirks = [json.dumps(quirk, ensure_ascii=False) for quirk in spokes["data_quirks"]]
    results = [
        check(f"Parcelly PSM hidden rule: {name}", found(pattern, hidden))
        for name, pattern in HIDDEN_RULES.items()
    ]
    results.append(
        check("Spokes PSM hook ORDER_PAID_HANDLERS", found("ORDER_PAID_HANDLERS", hooks))
    )
    results += [
        check(f"Spokes PSM quirk: {name}", found(pattern, quirks))
        for name, pattern in SPOKES_QUIRKS.items()
    ]
    results += [
        check(f"Parcelly brief rule: {name}", found(pattern, [briefs.get("parcelly", "")]))
        for name, pattern in HIDDEN_RULES.items()
    ]
    results.append(check("both briefs accepted", len(briefs) == 2))
    for party, brief in briefs.items():
        (STATE_ROOT / party / "brief.sent.json").write_text(brief, encoding="utf-8")
    return all(results)


def evaluate_negotiation(outcome: dict[str, Any]) -> bool:
    session = outcome["session"]
    for turn in outcome["turns"]:
        mark = "accepted" if turn["accepted"] else f"rejected {turn['errors'][:2]}"
        print(f"round {turn['round']} {turn['party']} {turn['status']} {mark}")
        print(f"    {turn['message'][:300]}")
    for question in outcome["questions"]:
        print(f"{question['id']} {question['party']}: {question['text']} -> {question['answer']}")
    CONTRACT.write_text(outcome["contract"], encoding="utf-8")
    print(f"final contract: {CONTRACT}")
    results = [
        check(
            f"AWAITING_SIGNATURES within {session['max_rounds']} rounds (state {session['state']},"
            f" round {session['round']})",
            any(
                event["type"] == "session.state_changed"
                and event["payload"]["to"] == "AWAITING_SIGNATURES"
                for event in outcome["events"]
            ),
        ),
        check(
            "no code crossed the boundary",
            session["metrics"]["code_lines_shared"] == 0,
        ),
        evaluate_contract(parse_yaml(outcome["contract"])),
    ]
    return all(results)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run both party runtimes against the broker")
    parser.add_argument("--sandbox", choices=["local", "tokenfactory", "docker"], default="docker")
    parser.add_argument("--mode", choices=["live", "record", "replay"], default="replay")
    parser.add_argument("--until", choices=sorted(ACTIVE_STATES), default="signatures")
    arguments = parser.parse_args()
    os.environ["DOUBLEBLIND_LLM_MODE"] = arguments.mode
    os.environ["DOUBLEBLIND_CASSETTE"] = str(CASSETTE)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        local_root = Path(directory) / "sandbox"
        try:
            outcome = run_agents(arguments.sandbox, local_root, arguments.until)
        finally:
            LocalSandboxProvider(local_root).remove_all()
    events = outcome["events"]
    ok = evaluate_briefing(events)
    if arguments.until == "signatures":
        ok = evaluate_negotiation(outcome) and ok
    blocked = [event["payload"] for event in events if event["type"].startswith("egress.")]
    print(f"egress events: {blocked}")
    cost = sum(event["payload"]["cost_usd"] for event in events if event["type"] == "llm.call")
    print(f"model cost of this run: ${cost:.4f}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
