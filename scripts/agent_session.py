import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
import threading
from contextlib import nullcontext
from pathlib import Path
from typing import Any

import httpx
from demo_workspace import REPO_ROOT
from eval_negotiation import CONTRACT
from eval_negotiation import evaluate as evaluate_contract
from sandbox_party import PARTIES, SandboxKind, base_heads, sandbox_factory
from scripted_party import ScriptedParty
from scripted_session import PRESET, dashboard_link, hold_broker, running_broker
from tunnel import wait_until_reachable

from doubleblind_agent.main import PartyConfig, PartyRuntime
from doubleblind_broker.db import build_engine
from doubleblind_broker.e2e import SandboxGatewayE2E
from doubleblind_broker.service import BrokerService, BrokerSettings
from doubleblind_contract.loader import parse_yaml
from doubleblind_contract.runtime import free_port
from doubleblind_sandbox.environment import gateway_url
from doubleblind_sandbox.isolation_probe import SandboxIsolationProbe
from doubleblind_sandbox.local import LocalSandboxProvider

STATE_ROOT = REPO_ROOT / "volumes" / "state"
RECORDINGS = REPO_ROOT / "demo" / "recordings"
ACTIVE_STATES = {
    "negotiating": {"CREATED", "BRIEFING"},
    "signatures": {"CREATED", "BRIEFING", "NEGOTIATING", "AMENDING"},
    "implemented": {
        "CREATED",
        "BRIEFING",
        "NEGOTIATING",
        "AMENDING",
        "AWAITING_SIGNATURES",
        "IMPLEMENTING",
    },
}
ACTIVE_STATES["certified"] = ACTIVE_STATES["implemented"] | {"E2E"}
ACTIONS = {
    "negotiating": frozenset({"build_psm_and_brief"}),
    "signatures": frozenset({"build_psm_and_brief", "negotiate", "amend"}),
    "implemented": frozenset(
        {"build_psm_and_brief", "negotiate", "amend", "implement", "fix_after_e2e"}
    ),
}
ACTIONS["certified"] = ACTIONS["implemented"]
CODING_ACTIONS = frozenset({"implement", "fix_after_e2e"})
HIDDEN_RULES = {
    "pallet above 25 kg": r"pallet",
    "size from weight": r"\bsize\b|SIZE_WEIGHT",
    "idempotency": r"idempoten",
    "postcode matches city": r"(?=.*(postcode|postal))(?=.*city)",
    "phone and postcode format": r"\+447|E\.164|phone",
    "signed webhooks": r"signature|hmac",
}
SPOKES_QUIRKS = {"address": r"address", "phone": r"phone", "weight": r"weight"}


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
    actions: frozenset[str],
) -> list[PartyRuntime]:
    runtimes = [
        PartyRuntime(
            PartyConfig(
                url, tokens[f"agent_{party}"], session_id, party, bases[party], STATE_ROOT / party
            ),
            factory,
            actions,
        )
        for party in PARTIES
    ]
    tasks = [asyncio.create_task(runtime.run()) for runtime in runtimes]
    async with httpx.AsyncClient(
        base_url=url, headers={"Authorization": f"Bearer {tokens['observer']}"}
    ) as viewer:
        resumes = 0
        escalated_polls = 0
        while not all(task.done() for task in tasks):
            if any(task.done() and task.exception() for task in tasks):
                break
            if any(task.done() for task in tasks) and all(
                task.done() or runtime.idle for task, runtime in zip(tasks, runtimes, strict=True)
            ):
                break
            state = (await viewer.get(f"/api/v1/sessions/{session_id}")).json()["state"]
            escalated_polls = escalated_polls + 1 if state == "ESCALATED" else 0
            if state == "ESCALATED" and resumes < MAX_RESUMES:
                if escalated_polls >= ESCALATION_WAIT_POLLS:
                    response = await viewer.post(
                        f"/api/v1/sessions/{session_id}/resume", json={"extra_rounds": EXTRA_ROUNDS}
                    )
                    resumes += response.is_success
            elif state not in ACTIVE_STATES[until]:
                break
            await asyncio.sleep(1.0)
    for task in tasks:
        task.cancel()
    for result in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
            raise result
    return runtimes


class HandOver(Exception):
    pass


class StopBeforeCoding:
    def implement(self, contract_yaml: str, secrets: Any, side: Any) -> dict[str, Any]:
        raise HandOver


def negotiate_with_scripts(url: str, session_id: str, tokens: dict[str, str]) -> None:
    def play(party: str) -> None:
        headers = {"Authorization": f"Bearer {tokens[f'agent_{party}']}"}
        with httpx.Client(base_url=url, timeout=60.0, headers=headers) as client:
            try:
                ScriptedParty(client, session_id, party, StopBeforeCoding()).run(600.0)
            except HandOver:
                return

    threads = [threading.Thread(target=play, args=(party,)) for party in PARTIES]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()


SCRIPTED_ROUNDS = 14
EXTRA_ROUNDS = 6
ESCALATION_WAIT_POLLS = 3
MAX_RESUMES = 2
AGENT_ROUNDS = 14


def run_agents(
    kind: SandboxKind,
    local_root: Path,
    until: str,
    scripted: bool,
    name: str = "agents",
    hold: bool = False,
    broker: str | None = None,
    publish: bool = False,
) -> dict[str, Any]:
    factory = sandbox_factory(kind, local_root)
    bases = base_heads(kind, local_root)
    settings = BrokerSettings(
        presets_dir=REPO_ROOT / "demo" / "presets",
        max_rounds=SCRIPTED_ROUNDS if scripted else AGENT_ROUNDS,
        autopilot=True,
        autopilot_delay_s=0.0,
        next_action_wait_s=2.0,
    )
    port = int(os.environ.get("BROKER_PORT", "0")) or free_port()
    gateway = gateway_url(kind, port)
    executor = SandboxGatewayE2E(factory, gateway) if gateway else None
    probe = (
        SandboxIsolationProbe(factory, bases, kind == "tokenfactory") if kind != "local" else None
    )
    service = BrokerService(build_engine("sqlite://"), settings, executor, None, probe)
    host = "0.0.0.0" if kind == "docker" else "127.0.0.1"
    serving = nullcontext(broker.rstrip("/")) if broker else running_broker(service, port, host)
    admin_token = os.environ.get("DOUBLEBLIND_ADMIN_TOKEN")
    as_admin = {"Authorization": f"Bearer {admin_token}"} if admin_token else {}
    with serving as url, httpx.Client(base_url=url, timeout=60.0) as admin:
        if kind == "tokenfactory" and gateway and broker is None:
            wait_until_reachable(gateway)
        created = admin.post(
            "/api/v1/sessions", json={"name": name, "preset": PRESET}, headers=as_admin
        ).json()
        session_id, tokens = created["session_id"], created["tokens"]
        observer = {"Authorization": f"Bearer {tokens['observer']}"}
        sessions = f"/api/v1/sessions/{session_id}"
        admin.post(f"{sessions}/start", headers=observer).raise_for_status()
        print(f"dashboard: {dashboard_link(url, session_id, tokens)}", flush=True)
        if publish:
            public = admin.post(f"{sessions}/publish", headers=as_admin or observer).json()
            print(f"public: {url}/#/s/{session_id}?observer={public['public_token']}", flush=True)
        actions = ACTIONS[until]
        if scripted:
            negotiate_with_scripts(url, session_id, tokens)
            actions = CODING_ACTIONS
        if until in ("implemented", "certified"):
            for party in PARTIES:
                (STATE_ROOT / party / "workspace.json").unlink(missing_ok=True)
        runtimes = asyncio.run(run_parties(url, session_id, tokens, bases, factory, until, actions))
        for runtime in runtimes:
            for line in runtime.log:
                print(f"[{runtime.config.party}] {line}")
        outcome = {
            "session": admin.get(sessions, headers=observer).json(),
            "events": admin.get(f"{sessions}/events", headers=observer).json(),
            "turns": admin.get(f"{sessions}/turns", headers=observer).json(),
            "questions": admin.get(f"{sessions}/questions", headers=observer).json(),
            "contract": admin.get(f"{sessions}/contract", headers=observer).text,
            "certificate": admin.get(f"{sessions}/certificate", headers=observer).json(),
            "probe": admin.post(f"{sessions}/isolation-probe", headers=observer).json(),
        }
        if hold:
            hold_broker(url)
        return outcome


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
    lookups = [
        event["payload"] for event in outcome["events"] if event["type"] == "tool.lookup_standard"
    ]
    if lookups or os.environ.get("DOUBLEBLIND_TAVILY") == "1":
        sources = {url for item in lookups for url in item["sources"]}
        cited = [
            decision["id"]
            for decision in parse_yaml(outcome["contract"]).get("decisions", [])
            if any(url in decision["text"] for url in sources)
        ]
        print(f"Tavily lookups: {[item['query'] for item in lookups]}")
        results.append(check(f"a decision cites a Tavily source: {cited}", bool(cited)))
    return all(results)


def evaluate_implementation(outcome: dict[str, Any]) -> bool:
    contract = parse_yaml(outcome["contract"])
    events = outcome["events"]
    results = []
    cycles = []
    for party, side in (("spokes", "consumer"), ("parcelly", "provider")):
        required = {item["id"] for item in contract["scenarios"] if item["side"] == side}
        runs = [
            event["payload"]
            for event in events
            if event["type"] == "tests.result" and event["payload"]["party"] == party
        ]
        passed = (
            {item["scenario_id"] for item in runs[-1]["results"] if item["status"] == "passed"}
            if runs
            else set()
        )
        missing = sorted(required - passed)
        results.append(
            check(
                f"{party}: all {len(required)} {side} scenarios green"
                + (f" (missing {', '.join(missing)})" if missing else ""),
                not missing,
            )
        )
        notes = [
            event["payload"]["note"]
            for event in events
            if event["type"] == "coder.progress" and event["payload"]["party"] == party
        ]
        steps = [event for event in events if event["type"] == "coder.progress"]
        counts = [re.findall(r"(\d+)/(\d+) green", note) for note in notes]
        red_then_green = not missing and any(
            found and found[0][0] != found[0][1] for found in counts
        )
        test_runs = [note for note in notes if "green" in note]
        print(f"{party}: {len(notes)} of {len(steps)} coder steps, test runs: {test_runs}")
        cycles.append(red_then_green)
    results.append(check("red -> triage -> fix -> green cycle seen", any(cycles)))
    return all(results)


def evaluate_certification(outcome: dict[str, Any]) -> bool:
    certificate = outcome["certificate"]
    events = outcome["events"]
    last = max((i for i, event in enumerate(events) if event["type"] == "e2e.started"), default=0)
    gateway = [event["payload"] for event in events[last:] if event["type"] == "gateway.request"]
    for event in outcome["events"]:
        if event["type"] == "e2e.result":
            statuses = [
                f"{item['id']} {item['status']}"
                for item in event["payload"]["report"].get("scenarios", [])
            ]
            print(f"e2e: passed={event['payload']['passed']} {statuses}")
    print(f"gateway: {certificate['gateway']}, costs: {certificate['costs']}")
    results = [
        check(f"CERTIFIED (state {certificate['state']})", certificate["certified"]),
        check("E2E traffic went through the gateway", bool(gateway)),
        check(
            "every gateway request matched the contract",
            all(item["allowed"] and not item.get("violations") for item in gateway),
        ),
        check("request outside the contract refused", outcome["probe"]["ok"]),
        check("code_lines_shared == 0", certificate["metrics"]["code_lines_shared"] == 0),
        check("audit chain verified", certificate["audit"]["ok"]),
    ]
    return all(results)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run both party runtimes against the broker")
    parser.add_argument("--sandbox", choices=["local", "tokenfactory", "docker"], default="docker")
    parser.add_argument("--mode", choices=["live", "record", "replay"], default="replay")
    parser.add_argument("--until", choices=sorted(ACTIVE_STATES), default="signatures")
    parser.add_argument("--negotiation", choices=["agents", "scripted"], default="agents")
    arguments = parser.parse_args()
    os.environ["DOUBLEBLIND_LLM_MODE"] = arguments.mode
    os.environ["DOUBLEBLIND_CASSETTE"] = str(
        RECORDINGS / "spokes-parcelly.jsonl"
        if arguments.negotiation == "agents"
        else RECORDINGS / "spokes-parcelly-demo.jsonl"
    )
    os.environ["DOUBLEBLIND_TAVILY_RECORDING"] = str(RECORDINGS / "tavily.jsonl")
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        local_root = Path(directory) / "sandbox"
        try:
            outcome = run_agents(
                arguments.sandbox,
                local_root,
                arguments.until,
                arguments.negotiation == "scripted",
            )
        finally:
            LocalSandboxProvider(local_root).remove_all()
    events = outcome["events"]
    ok = True
    if arguments.negotiation == "agents":
        ok = evaluate_briefing(events)
        if arguments.until != "negotiating":
            ok = evaluate_negotiation(outcome) and ok
    if arguments.until in ("implemented", "certified"):
        ok = evaluate_implementation(outcome) and ok
    if arguments.until == "certified":
        ok = evaluate_certification(outcome) and ok
    blocked = [event["payload"] for event in events if event["type"].startswith("egress.")]
    print(f"egress events: {blocked}")
    cost = sum(event["payload"]["cost_usd"] for event in events if event["type"] == "llm.call")
    print(f"model cost of this run: ${cost:.4f}")
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
