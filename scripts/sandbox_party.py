import asyncio
import json
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any, Literal

from demo_workspace import BINDING_FILE, DEMO_DIR, REFERENCE_DIR, REPO_ROOT, side_env
from scripted_party import payload_from_report, reference_binding

from doubleblind_contract.models import Side
from doubleblind_contract.runtime import secret_env_name
from doubleblind_llm.budget import Ledger, ledger_path_from_env
from doubleblind_llm.settings import load_dotenv
from doubleblind_sandbox.environment import recorded_bases, remote_sandbox
from doubleblind_sandbox.images import repo_files
from doubleblind_sandbox.local import LocalSandboxProvider
from doubleblind_sandbox.provider import REPO_DIR, ServiceSandbox, Workspace, repo_path

SandboxKind = Literal["local", "tokenfactory", "docker"]
SandboxFactory = Callable[[], ServiceSandbox]
ENTRY = REPO_ROOT / "scripts" / "contract_test_entry.py"
WORK_DIR = ".doubleblind"
TEST_TIMEOUT_S = 600
PARTIES = ("spokes", "parcelly")


def local_sandbox(root: Path) -> LocalSandboxProvider:
    return LocalSandboxProvider(
        root, {f"{REPO_DIR}/node_modules": DEMO_DIR / "parcelly" / "node_modules"}
    )


def sandbox_factory(kind: SandboxKind, local_root: Path) -> SandboxFactory:
    if kind == "local":
        return lambda: local_sandbox(local_root)
    if kind == "tokenfactory":
        load_dotenv(REPO_ROOT / ".env")
    return remote_sandbox(kind)


async def import_local_bases(provider: LocalSandboxProvider) -> dict[str, str]:
    return {
        party: await provider.import_directory(DEMO_DIR / party, tag=f"doubleblind-{party}-base")
        for party in PARTIES
    }


def base_heads(kind: SandboxKind, local_root: Path) -> dict[str, str]:
    if kind == "local":
        return asyncio.run(import_local_bases(local_sandbox(local_root)))
    bases = recorded_bases(kind)
    if bases is None:
        raise RuntimeError(f"no base images for {kind}; run scripts/build_base_images.py first")
    return bases


class ContractTestsInSandbox:
    def __init__(self, factory: SandboxFactory, party_key: str, base: str) -> None:
        self.factory = factory
        self.party_key = party_key
        self.base = base
        self.workspace: Workspace | None = None

    def implement(
        self, contract_yaml: str, secrets: Mapping[str, str], side: Side
    ) -> dict[str, Any]:
        return asyncio.run(self.implement_in_sandbox(contract_yaml, secrets, side))

    async def apply_reference(self, sandbox: ServiceSandbox) -> Workspace:
        if self.workspace is None:
            workspace = await sandbox.open_workspace(self.party_key, self.base)
            await sandbox.write_files(workspace, repo_files(REFERENCE_DIR / self.party_key))
            self.workspace = workspace
        return self.workspace

    async def implement_in_sandbox(
        self, contract_yaml: str, secrets: Mapping[str, str], side: Side
    ) -> dict[str, Any]:
        sandbox = self.factory()
        try:
            workspace = await self.apply_reference(sandbox)
            env = side_env(side)
            env.update({secret_env_name(name): value for name, value in secrets.items()})
            result = await sandbox.run(
                workspace,
                f"{sandbox.python} {WORK_DIR}/entry.py {side} {WORK_DIR}/contract.yaml"
                f" {BINDING_FILE} {WORK_DIR}/report.json",
                files={
                    repo_path(f"{WORK_DIR}/entry.py"): ENTRY.read_bytes(),
                    repo_path(f"{WORK_DIR}/contract.yaml"): contract_yaml.encode("utf-8"),
                },
                env=env,
                timeout_s=TEST_TIMEOUT_S,
            )
        finally:
            await sandbox.aclose()
        lines = result.stdout.strip().splitlines()
        outcome = json.loads(lines[-1]) if result.ok and lines else {}
        report = outcome.get("report")
        if report is None:
            details = outcome.get("output") or result.output()
            raise RuntimeError(f"db-contract test did not produce a report: {details[-800:]}")
        payload = payload_from_report(report, reference_binding(self.party_key))
        payload["workspace_image"] = workspace.head
        return payload


def sandbox_cost_usd() -> float:
    ledger = Ledger(ledger_path_from_env(REPO_ROOT))
    return sum(entry.cost_usd for entry in ledger.entries() if entry.role.startswith("sandbox:"))
