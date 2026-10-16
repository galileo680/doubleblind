import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from agent_session import STATE_ROOT, check, evaluate_certification, run_agents
from demo_workspace import REPO_ROOT
from sandbox_party import PARTIES, SandboxKind
from tunnel import public_url

from doubleblind_contract.runtime import free_port
from doubleblind_sandbox.local import LocalSandboxProvider

LEDGER = REPO_ROOT / "volumes" / "llm-ledger.jsonl"
TRACE_DIR = REPO_ROOT / "volumes" / "demo-traces"
SESSION_NAME = "Spokes ↔ Parcelly shipping"


CASSETTE = REPO_ROOT / "demo" / "recordings" / "spokes-parcelly.jsonl"


def model_calls() -> int:
    if not LEDGER.is_file():
        return 0
    rows = [json.loads(line) for line in LEDGER.read_text(encoding="utf-8").splitlines() if line]
    return sum(1 for row in rows if not row["role"].startswith("sandbox"))


def reset() -> None:
    for party in PARTIES:
        shutil.rmtree(STATE_ROOT / party, ignore_errors=True)
    (REPO_ROOT / "volumes" / "broker.db").unlink(missing_ok=True)
    docker = shutil.which("docker")
    if docker is None:
        return
    listed = subprocess.run(
        [docker, "images", "--format", "{{.Repository}}"],
        capture_output=True,
        text=True,
        check=False,
    )
    images = sorted(
        {
            name
            for name in listed.stdout.split()
            if name.startswith("doubleblind-") and "-ses_" in name
        }
    )
    if images:
        subprocess.run([docker, "rmi", "--force", *images], capture_output=True, check=False)
    print(f"reset: party state, broker database and {len(images)} session images removed")


def step(event: dict[str, Any]) -> str:
    if event["type"] == "gateway.request":
        return f"gateway.request {event['payload'].get('source')}"
    return str(event["type"])


def without_polling(steps: list[str]) -> list[str]:
    return [
        item
        for index, item in enumerate(steps)
        if not (item.startswith("gateway.request") and index and item == steps[index - 1])
    ]


def trace(outcome: dict[str, Any]) -> dict[str, Any]:
    events = outcome["events"]
    per_party = {
        party: without_polling(
            [
                step(event)
                for event in events
                if event["actor"] in (party, f"human:{party}")
                or event["payload"].get("party") == party
            ]
        )
        for party in PARTIES
    }
    return {
        "states": [
            event["payload"]["to"] for event in events if event["type"] == "session.state_changed"
        ],
        **per_party,
        "contract": hashlib.sha256(outcome["contract"].encode("utf-8")).hexdigest(),
    }


def evaluate(outcome: dict[str, Any], replay: bool, paid_calls: int) -> bool:
    probe = outcome["probe"]
    for result in probe["results"]:
        print(f"probe {result['status'].upper():<8} {result['name']} · {result['actual']}")
    results = [
        evaluate_certification(outcome),
        check(
            "isolation probes as expected (no failed probe)",
            all(result["status"] != "failed" for result in probe["results"]),
        ),
    ]
    if replay:
        results.append(check(f"no model calls ({paid_calls} paid)", paid_calls == 0))
    return all(results)


def run_once(
    sandbox: SandboxKind, until_hold: bool, broker: str | None, publish: bool
) -> dict[str, Any]:
    reset()
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        local_root = Path(directory) / "sandbox"
        try:
            return run_agents(
                sandbox, local_root, "certified", False, SESSION_NAME, until_hold, broker, publish
            )
        finally:
            LocalSandboxProvider(local_root).remove_all()


def main() -> None:
    parser = argparse.ArgumentParser(description="Record or replay the DoubleBlind demo session")
    parser.add_argument("command", choices=["record", "replay", "live", "reset"])
    parser.add_argument("--sandbox", choices=["tokenfactory", "docker"], default="tokenfactory")
    parser.add_argument("--repeat", type=int, default=1, help="replay N times and compare traces")
    parser.add_argument("--pace", type=int, default=0, help="delay per replayed model answer, ms")
    parser.add_argument("--hold", action="store_true", help="keep the dashboard running afterwards")
    parser.add_argument("--broker", help="use a running broker (e.g. the public one) by its URL")
    parser.add_argument("--publish", action="store_true", help="make the session publicly viewable")
    arguments = parser.parse_args()
    if arguments.command == "reset":
        reset()
        return
    replay = arguments.command == "replay"
    os.environ["DOUBLEBLIND_LLM_MODE"] = arguments.command
    os.environ["DOUBLEBLIND_CASSETTE"] = str(CASSETTE)
    os.environ["DOUBLEBLIND_REPLAY_DELAY_MS"] = str(arguments.pace)
    if replay and not CASSETTE.is_file():
        raise SystemExit(f"no recording at {CASSETTE}; run 'record' first")
    port = int(os.environ.get("BROKER_PORT", "0")) or free_port()
    os.environ["BROKER_PORT"] = str(port)
    tunnel = (
        public_url(port)
        if arguments.sandbox == "tokenfactory" and not arguments.broker
        else nullcontext()
    )
    traces: list[str] = []
    ok = True
    with tunnel:
        for run in range(1, arguments.repeat + 1):
            print(
                f"== {arguments.command} {run}/{arguments.repeat} on {arguments.sandbox}",
                flush=True,
            )
            before = model_calls()
            outcome = run_once(
                arguments.sandbox,
                arguments.hold and run == arguments.repeat,
                arguments.broker,
                arguments.publish,
            )
            ok = evaluate(outcome, replay, model_calls() - before) and ok
            digest = json.dumps(trace(outcome), sort_keys=True, indent=1)
            TRACE_DIR.mkdir(parents=True, exist_ok=True)
            (TRACE_DIR / f"{arguments.sandbox}-{run}.json").write_text(digest, encoding="utf-8")
            (TRACE_DIR / f"{arguments.sandbox}-{run}-events.json").write_text(
                json.dumps(outcome["events"], ensure_ascii=False, indent=1), encoding="utf-8"
            )
            traces.append(hashlib.sha256(digest.encode("utf-8")).hexdigest())
            print(f"trace {traces[-1][:16]} · contract {trace(outcome)['contract'][:16]}")
    if arguments.repeat > 1:
        ok = check(f"{arguments.repeat} runs give the same trace", len(set(traces)) == 1) and ok
    if not ok:
        sys.exit(1)


if __name__ == "__main__":
    main()
