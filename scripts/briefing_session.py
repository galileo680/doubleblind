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
from sandbox_party import PARTIES, SandboxKind, base_heads, sandbox_factory
from scripted_session import PRESET, running_broker

from doubleblind_agent.main import PartyConfig, PartyRuntime
from doubleblind_broker.db import build_engine
from doubleblind_broker.service import BrokerService, BrokerSettings
from doubleblind_sandbox.local import LocalSandboxProvider

STATE_ROOT = REPO_ROOT / "volumes" / "state"
CASSETTE = REPO_ROOT / "demo" / "recordings" / "briefing.jsonl"
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
    url: str, session_id: str, tokens: dict[str, str], bases: dict[str, str], factory: Any
) -> list[PartyRuntime]:
    runtimes = [
        PartyRuntime(
            PartyConfig(
                url, tokens[f"agent_{party}"], session_id, party, bases[party], STATE_ROOT / party
            ),
            factory,
        )
        for party in PARTIES
    ]
    tasks = [asyncio.create_task(runtime.run()) for runtime in runtimes]
    async with httpx.AsyncClient(
        base_url=url, headers={"Authorization": f"Bearer {tokens['observer']}"}
    ) as viewer:
        while not any(task.done() for task in tasks):
            state = (await viewer.get(f"/api/v1/sessions/{session_id}")).json()["state"]
            if state != "BRIEFING":
                break
            await asyncio.sleep(1.0)
    for task in tasks:
        task.cancel()
    for result in await asyncio.gather(*tasks, return_exceptions=True):
        if isinstance(result, Exception) and not isinstance(result, asyncio.CancelledError):
            raise result
    return runtimes


def run_briefing(kind: SandboxKind, local_root: Path) -> list[dict[str, Any]]:
    factory = sandbox_factory(kind, local_root)
    bases = base_heads(kind, local_root)
    settings = BrokerSettings(presets_dir=REPO_ROOT / "demo" / "presets", next_action_wait_s=2.0)
    service = BrokerService(build_engine("sqlite://"), settings)
    with running_broker(service) as url, httpx.Client(base_url=url, timeout=60.0) as admin:
        created = admin.post("/api/v1/sessions", json={"name": "briefing", "preset": PRESET}).json()
        session_id, tokens = created["session_id"], created["tokens"]
        observer = {"Authorization": f"Bearer {tokens['observer']}"}
        admin.post(f"/api/v1/sessions/{session_id}/start", headers=observer).raise_for_status()
        runtimes = asyncio.run(run_parties(url, session_id, tokens, bases, factory))
        for runtime in runtimes:
            for line in runtime.log:
                print(f"[{runtime.config.party}] {line}")
        events: list[dict[str, Any]] = admin.get(
            f"/api/v1/sessions/{session_id}/events", headers=observer
        ).json()
    return events


def evaluate(events: list[dict[str, Any]]) -> bool:
    parcelly, spokes = load("parcelly", "psm.json"), load("spokes", "psm.json")
    brief = load("parcelly", "brief.json")
    hidden = [rule["rule"] for rule in parcelly["business_rules"] if not rule["public"]]
    brief_text = [json.dumps(brief, ensure_ascii=False)]
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
        check(f"Parcelly brief rule: {name}", found(pattern, brief_text))
        for name, pattern in HIDDEN_RULES.items()
    ]
    types = [event["type"] for event in events]
    results.append(check("both briefs accepted", types.count("brief.submitted") == 2))
    results.append(
        check(
            "session moved to NEGOTIATING",
            any(
                event["type"] == "session.state_changed" and event["payload"]["to"] == "NEGOTIATING"
                for event in events
            ),
        )
    )
    blocked = [event["payload"] for event in events if event["type"].startswith("egress.")]
    print(f"egress events: {blocked}")
    cost = sum(event["payload"]["cost_usd"] for event in events if event["type"] == "llm.call")
    print(f"model cost of this run: ${cost:.4f}")
    for event in events:
        if event["type"] == "brief.submitted":
            sent = STATE_ROOT / event["payload"]["party"] / "brief.sent.json"
            sent.write_text(
                json.dumps(event["payload"]["brief"], ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            print(f"brief received by the broker: {sent}")
    return all(results)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run both party runtimes until their briefs are accepted"
    )
    parser.add_argument("--sandbox", choices=["local", "tokenfactory", "docker"], default="docker")
    parser.add_argument("--mode", choices=["live", "record", "replay"], default="replay")
    arguments = parser.parse_args()
    os.environ["DOUBLEBLIND_LLM_MODE"] = arguments.mode
    os.environ["DOUBLEBLIND_CASSETTE"] = str(CASSETTE)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        local_root = Path(directory) / "sandbox"
        try:
            events = run_briefing(arguments.sandbox, local_root)
        finally:
            LocalSandboxProvider(local_root).remove_all()
    if not evaluate(events):
        sys.exit(1)


if __name__ == "__main__":
    main()
