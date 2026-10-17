from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Protocol

import doubleblind_sandbox.relay_client as relay_client

REPO_DIR = "/workspace/repo"
DEFAULT_TIMEOUT_S = 300
SERVICE_TIMEOUT_S = 900
ProbeStatus = Literal["passed", "failed", "skipped"]


class SandboxError(Exception):
    pass


@dataclass
class Workspace:
    party: str
    head: str
    history: list[str] = field(default_factory=list)

    def advance(self, new_head: str) -> None:
        self.head = new_head
        self.history.append(new_head)


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_s: float = 0.0
    cost_usd: float = 0.0
    new_head: str | None = None

    @property
    def ok(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def output(self) -> str:
        return f"{self.stdout}{self.stderr}"


@dataclass(frozen=True)
class ServiceHandle:
    id: str
    party: str


@dataclass(frozen=True)
class ProbeResult:
    name: str
    expected: str
    actual: str
    status: ProbeStatus

    def to_dict(self) -> dict[str, str]:
        return {
            "name": self.name,
            "expected": self.expected,
            "actual": self.actual,
            "status": self.status,
        }


class SandboxProvider(Protocol):
    name: str
    spent_usd: float

    async def open_workspace(self, party: str, base: str) -> Workspace: ...

    async def run(
        self,
        workspace: Workspace,
        command: str,
        *,
        persist: bool = False,
        network: bool = False,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        files: Mapping[str, bytes] | None = None,
        cwd: str = REPO_DIR,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult: ...

    async def read_file(self, workspace: Workspace, path: str) -> bytes: ...

    async def write_files(self, workspace: Workspace, files: Mapping[str, bytes]) -> ExecResult: ...

    async def tag(self, workspace: Workspace, tag: str) -> None: ...

    async def find_tag(self, tag: str) -> str | None: ...

    async def aclose(self) -> None: ...


class ServiceSandbox(SandboxProvider, Protocol):
    python: str

    async def start_service(
        self,
        workspace: Workspace,
        command: str,
        *,
        network: bool = True,
        timeout_s: int = SERVICE_TIMEOUT_S,
        env: Mapping[str, str] | None = None,
        files: Mapping[str, bytes] | None = None,
    ) -> ServiceHandle: ...

    async def stop_service(self, handle: ServiceHandle) -> None: ...


def branch(workspace: Workspace) -> Workspace:
    return Workspace(workspace.party, workspace.head, list(workspace.history))


def repo_path(relative: str) -> str:
    return f"{REPO_DIR}/{relative.lstrip('/')}"


RELAY_FILE = ".doubleblind-relay.py"
RELAY_SOURCE = Path(relay_client.__file__).read_bytes()


async def start_relayed_app(
    sandbox: ServiceSandbox,
    workspace: Workspace,
    broker_url: str,
    session_id: str,
    party: str,
    port: int,
    env: Mapping[str, str],
) -> ServiceHandle:
    command = (
        f"{sandbox.python} {RELAY_FILE} --broker {broker_url} --session {session_id}"
        f" --party {party} --target http://127.0.0.1:{port}"
    )
    return await sandbox.start_service(
        workspace, command, env=env, files={repo_path(RELAY_FILE): RELAY_SOURCE}
    )
