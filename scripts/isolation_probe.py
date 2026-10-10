import argparse
import sys
import tempfile
from pathlib import Path
from typing import Any

import httpx
from sandbox_party import SandboxKind, base_heads, sandbox_cost_usd, sandbox_factory
from scripted_session import run_quick_session

from doubleblind_sandbox.isolation_probe import SandboxIsolationProbe
from doubleblind_sandbox.local import LocalSandboxProvider


def print_results(payload: dict[str, Any]) -> None:
    width = max(len(result["name"]) for result in payload["results"])
    for result in payload["results"]:
        print(
            f"{result['status'].upper():<8} {result['name']:<{width}}"
            f"  expected: {result['expected']:<18} actual: {result['actual']}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Prove the isolation guarantees of DoubleBlind")
    parser.add_argument("--sandbox", choices=["tokenfactory", "local"], default="tokenfactory")
    arguments = parser.parse_args()
    kind: SandboxKind = arguments.sandbox
    root = Path(tempfile.mkdtemp(prefix="doubleblind-probe-"))
    probe = SandboxIsolationProbe(sandbox_factory(kind, root), base_heads(kind, root))
    collected: dict[str, Any] = {}

    def run_probe(client: httpx.Client, session_id: str) -> None:
        response = client.post(f"/api/v1/sessions/{session_id}/isolation-probe")
        response.raise_for_status()
        collected.update(response.json())

    cost_before = sandbox_cost_usd()
    try:
        outcome = run_quick_session(isolation_probe=probe, after=run_probe)
    finally:
        if kind == "local":
            LocalSandboxProvider(root).remove_all()
    print(f"session state: {outcome.state}")
    print_results(collected)
    print(f"sandbox cost of the probes: ${sandbox_cost_usd() - cost_before:.4f}")
    if not collected.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
