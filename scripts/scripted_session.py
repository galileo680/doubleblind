import argparse
import copy
import os
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import httpx
import uvicorn
from demo_workspace import REPO_ROOT, Workspace, build_workspace
from sandbox_party import (
    PARTIES,
    ContractTestsInSandbox,
    SandboxKind,
    base_heads,
    sandbox_cost_usd,
    sandbox_factory,
)
from scripted_party import (
    ContractTestsInWorkspace,
    Implementer,
    ReportedGreen,
    ScriptedParty,
    reference_contract,
)

from doubleblind_broker.app import create_app
from doubleblind_broker.autopilot import DefaultOptionSimulator
from doubleblind_broker.db import build_engine
from doubleblind_broker.e2e import E2EExecutor, E2ERequest, LocalProcessE2E, SandboxGatewayE2E
from doubleblind_broker.service import BrokerService, BrokerSettings, IsolationProbe
from doubleblind_contract.loader import load_contract_text, parse_yaml
from doubleblind_contract.runtime import RunReport, ScenarioResult, free_port, utc_timestamp
from doubleblind_sandbox.environment import gateway_url
from doubleblind_sandbox.local import LocalSandboxProvider

PRESET = "spokes-parcelly"
E2EMode = Literal["gateway", "cleanroom"]


class ReportedGreenE2E:
    def run(self, request: E2ERequest) -> RunReport:
        contract = load_contract_text(request.contract_yaml)
        report = RunReport(contract.id, contract.version, "e2e", utc_timestamp())
        report.scenarios = [
            ScenarioResult(item.id, item.title, "passed", 0)
            for item in contract.scenarios_for("e2e")
        ]
        report.finished_at = utc_timestamp()
        return report


def normalized(contract: dict[str, Any]) -> dict[str, Any]:
    data = copy.deepcopy(contract)
    for key in ("decisions", "scenarios"):
        items = data.get(key, [])
        for item in items:
            if isinstance(item, dict):
                item.pop("origin_question", None)
        data[key] = sorted(items, key=lambda item: str(item.get("id", "")))
    return data


@dataclass
class SessionOutcome:
    state: str
    contract: dict[str, Any]
    certificate: dict[str, Any]
    events: list[dict[str, Any]]
    party_logs: dict[str, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def matches_reference(self) -> bool:
        return normalized(self.contract) == normalized(reference_contract())


@contextmanager
def running_broker(
    service: BrokerService, port: int | None = None, host: str = "127.0.0.1"
) -> Iterator[str]:
    port = port or free_port()
    server = uvicorn.Server(
        uvicorn.Config(create_app(service=service), host=host, port=port, log_level="warning")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 15
    while not server.started:
        if time.monotonic() > deadline:
            raise RuntimeError("the broker did not start")
        time.sleep(0.05)
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.should_exit = True
        thread.join(timeout=15)


def run_session(
    executor: E2EExecutor,
    implementers: dict[str, Implementer],
    autopilot_delay_s: float = 0.2,
    timeout_s: float = 600.0,
    port: int | None = None,
    host: str = "127.0.0.1",
    isolation_probe: IsolationProbe | None = None,
    after: Callable[[httpx.Client, str], None] | None = None,
) -> SessionOutcome:
    settings = BrokerSettings(
        presets_dir=REPO_ROOT / "demo" / "presets",
        autopilot=True,
        autopilot_delay_s=autopilot_delay_s,
        next_action_wait_s=5.0,
    )
    service = BrokerService(
        build_engine("sqlite://"), settings, executor, DefaultOptionSimulator(), isolation_probe
    )
    with running_broker(service, port, host) as broker_url:
        with httpx.Client(base_url=broker_url, timeout=60.0) as admin:
            created = admin.post("/api/v1/sessions", json={"name": "scripted", "preset": PRESET})
            created.raise_for_status()
            session_id, tokens = created.json()["session_id"], created.json()["tokens"]
            observer = {"Authorization": f"Bearer {tokens['observer']}"}
            admin.post(f"/api/v1/sessions/{session_id}/start", headers=observer).raise_for_status()
        parties: dict[str, ScriptedParty] = {}
        errors: list[str] = []
        stop = threading.Event()
        threads = []
        for party_key, implementer in implementers.items():
            client = httpx.Client(
                base_url=broker_url,
                timeout=60.0,
                headers={"Authorization": f"Bearer {tokens[f'agent_{party_key}']}"},
            )
            party = ScriptedParty(client, session_id, party_key, implementer, stop=stop)
            parties[party_key] = party

            def play(party: ScriptedParty = party) -> None:
                try:
                    party.run(timeout_s)
                except Exception as error:
                    errors.append(f"{party.party_key}: {type(error).__name__}: {error}")
                    stop.set()

            threads.append(threading.Thread(target=play, daemon=True))
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout_s)
        with httpx.Client(base_url=broker_url, timeout=600.0, headers=observer) as viewer:
            if after is not None:
                after(viewer, session_id)
            state = viewer.get(f"/api/v1/sessions/{session_id}").json()["state"]
            contract = parse_yaml(viewer.get(f"/api/v1/sessions/{session_id}/contract").text)
            certificate = viewer.get(f"/api/v1/sessions/{session_id}/certificate").json()
            events = viewer.get(f"/api/v1/sessions/{session_id}/events").json()
        for party in parties.values():
            party.client.close()
    return SessionOutcome(
        state, contract, certificate, events, {k: p.log for k, p in parties.items()}, errors
    )


def run_full_session(workspace: Workspace, timeout_s: float = 600.0) -> SessionOutcome:
    executor = LocalProcessE2E({"spokes": workspace.spokes, "parcelly": workspace.parcelly})
    implementers: dict[str, Implementer] = {
        "spokes": ContractTestsInWorkspace(workspace.spokes),
        "parcelly": ContractTestsInWorkspace(workspace.parcelly),
    }
    return run_session(executor, implementers, timeout_s=timeout_s)


def run_quick_session(
    timeout_s: float = 120.0,
    isolation_probe: IsolationProbe | None = None,
    after: Callable[[httpx.Client, str], None] | None = None,
) -> SessionOutcome:
    implementers: dict[str, Implementer] = {
        "spokes": ReportedGreen("spokes"),
        "parcelly": ReportedGreen("parcelly"),
    }
    return run_session(
        ReportedGreenE2E(),
        implementers,
        autopilot_delay_s=0.0,
        timeout_s=timeout_s,
        isolation_probe=isolation_probe,
        after=after,
    )


def run_sandbox_session(
    kind: SandboxKind, e2e_mode: E2EMode, timeout_s: float = 1200.0
) -> SessionOutcome:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        local_root = root / "sandbox"
        factory = sandbox_factory(kind, local_root)
        try:
            bases = base_heads(kind, local_root)
            implementers: dict[str, Implementer] = {
                party: ContractTestsInSandbox(factory, party, bases[party]) for party in PARTIES
            }
            if e2e_mode == "cleanroom":
                workspace = build_workspace(root / "cleanroom")
                try:
                    executor = LocalProcessE2E(
                        {"spokes": workspace.spokes, "parcelly": workspace.parcelly}
                    )
                    return run_session(executor, implementers, timeout_s=timeout_s)
                finally:
                    workspace.remove()
            port = int(os.environ.get("BROKER_PORT", "0")) or free_port()
            url = gateway_url(kind, port)
            if url is None:
                raise RuntimeError(
                    "Token Factory sandboxes reach the gateway only through BROKER_PUBLIC_URL;"
                    " set it or use --e2e cleanroom"
                )
            host = "0.0.0.0" if kind == "docker" else "127.0.0.1"
            gateway = SandboxGatewayE2E(factory, url)
            return run_session(gateway, implementers, timeout_s=timeout_s, port=port, host=host)
        finally:
            LocalSandboxProvider(local_root).remove_all()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a DoubleBlind session with scripted parties")
    parser.add_argument(
        "--sandbox",
        choices=["local", "tokenfactory", "docker"],
        default=os.environ.get("DOUBLEBLIND_SANDBOX") or None,
        help="run each party's code in its own sandbox",
    )
    parser.add_argument(
        "--e2e",
        choices=["gateway", "cleanroom"],
        default=os.environ.get("DOUBLEBLIND_E2E_MODE") or "gateway",
    )
    arguments = parser.parse_args()
    cost_before = sandbox_cost_usd()
    if arguments.sandbox:
        outcome = run_sandbox_session(arguments.sandbox, arguments.e2e)
    else:
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            workspace = build_workspace(Path(directory) / "workspace")
            try:
                outcome = run_full_session(workspace)
            finally:
                workspace.remove()
    for event in outcome.events:
        print(f"{event['seq']:>4} {event['type']:<26} {event['actor']}")
    for party, log in outcome.party_logs.items():
        for line in log:
            print(f"[{party}] {line}")
    for error in outcome.errors:
        print(f"ERROR {error}")
    print(f"final state: {outcome.state}")
    print(f"contract equals Appendix A: {outcome.matches_reference}")
    print(f"audit chain ok: {outcome.certificate['audit']['ok']}")
    print(f"sandbox cost of this run: ${sandbox_cost_usd() - cost_before:.4f}")
    if outcome.state != "CERTIFIED" or not outcome.matches_reference or outcome.errors:
        sys.exit(1)


if __name__ == "__main__":
    main()
