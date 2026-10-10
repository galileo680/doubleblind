import os
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from doubleblind_contract.runtime import free_port
from doubleblind_sandbox.local import link_directory, unlink_directory

REPO_ROOT = Path(__file__).resolve().parent.parent
DEMO_DIR = REPO_ROOT / "demo"
CONTRACT_TESTS_DIR = REPO_ROOT / "packages" / "contract" / "tests"
REFERENCE_DIR = CONTRACT_TESTS_DIR / "reference_impl"
REFERENCE_CONTRACT = CONTRACT_TESTS_DIR / "fixtures" / "spokes_parcelly_signed.yaml"
BINDING_FILE = "doubleblind.binding.yaml"
COPY_IGNORE = shutil.ignore_patterns(
    "node_modules", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache", "*.db"
)
SECRETS = {
    "DOUBLEBLIND_SECRET_PARTNER_API_KEY": "golden-partner-key",
    "DOUBLEBLIND_SECRET_WEBHOOK_SECRET": "golden-webhook-secret",
}


def side_env(side: str) -> dict[str, str]:
    port = free_port()
    prefix = "CONSUMER" if side == "consumer" else "PROVIDER"
    return {f"{prefix}_PORT": str(port), f"{prefix}_BASE_URL": f"http://127.0.0.1:{port}"}


@dataclass
class Workspace:
    root: Path

    @property
    def spokes(self) -> Path:
        return self.root / "spokes"

    @property
    def parcelly(self) -> Path:
        return self.root / "parcelly"

    @property
    def consumer_binding(self) -> Path:
        return self.spokes / BINDING_FILE

    @property
    def provider_binding(self) -> Path:
        return self.parcelly / BINDING_FILE

    def env(self) -> dict[str, str]:
        consumer, provider = side_env("consumer"), side_env("provider")
        return {
            **os.environ,
            **SECRETS,
            **consumer,
            **provider,
            "CONSUMER_WEBHOOK_URL": (
                f"{consumer['CONSUMER_BASE_URL']}/integrations/parcelly/webhook"
            ),
        }

    @contextmanager
    def mutated(self, relative_path: str, old: str, new: str) -> Iterator[None]:
        path = self.root / relative_path
        original = path.read_text(encoding="utf-8")
        if original.count(old) != 1:
            raise AssertionError(f"{relative_path} must contain {old!r} exactly once")
        path.write_text(original.replace(old, new), encoding="utf-8", newline="\n")
        try:
            yield
        finally:
            path.write_text(original, encoding="utf-8", newline="\n")

    def remove(self) -> None:
        unlink_directory(self.parcelly / "node_modules")
        shutil.rmtree(self.root, ignore_errors=True)


def build_workspace(root: Path, with_reference: bool = True) -> Workspace:
    workspace = Workspace(root)
    for party in ("spokes", "parcelly"):
        shutil.copytree(DEMO_DIR / party, root / party, ignore=COPY_IGNORE)
        if with_reference:
            shutil.copytree(REFERENCE_DIR / party, root / party, dirs_exist_ok=True)
        else:
            shutil.copy2(REFERENCE_DIR / party / BINDING_FILE, root / party / BINDING_FILE)
    link_directory(DEMO_DIR / "parcelly" / "node_modules", workspace.parcelly / "node_modules")
    return workspace


def run_repository_tests(workspace: Workspace) -> list[subprocess.CompletedProcess[str]]:
    npm = shutil.which("npm")
    if npm is None:
        raise AssertionError("npm is required for the golden test")
    commands = [
        ([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"], workspace.spokes),
        ([npm, "test"], workspace.parcelly),
    ]
    return [
        subprocess.run(command, cwd=cwd, capture_output=True, text=True, check=False)
        for command, cwd in commands
    ]
