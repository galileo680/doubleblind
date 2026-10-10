import asyncio
import shutil
import subprocess
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path

from doubleblind_sandbox.provider import (
    DEFAULT_TIMEOUT_S,
    REPO_DIR,
    SERVICE_TIMEOUT_S,
    ExecResult,
    SandboxError,
    ServiceHandle,
    Workspace,
)

HOST_ALIAS = "host.docker.internal:host-gateway"

Runner = Callable[[Sequence[str], float], subprocess.CompletedProcess[str]]


def run_command(arguments: Sequence[str], timeout_s: float) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_s,
        check=False,
    )


def docker_available() -> bool:
    executable = shutil.which("docker")
    if executable is None:
        return False
    try:
        return (
            run_command([executable, "info", "--format", "{{.ServerVersion}}"], 15).returncode == 0
        )
    except (OSError, subprocess.TimeoutExpired):
        return False


class DockerSandboxProvider:
    name = "docker"
    python = "python3"

    def __init__(self, runner: Runner = run_command) -> None:
        self.runner = runner

    def docker_command(
        self, arguments: Sequence[str], timeout_s: float = 120
    ) -> subprocess.CompletedProcess[str]:
        return self.runner(["docker", *arguments], timeout_s)

    def checked(self, arguments: Sequence[str], timeout_s: float = 120) -> str:
        completed = self.docker_command(arguments, timeout_s)
        if completed.returncode != 0:
            raise SandboxError(f"docker {arguments[0]} failed: {completed.stderr.strip()[:300]}")
        return completed.stdout.strip()

    def image(self, head: str) -> str:
        return head.removeprefix("tag:")

    async def open_workspace(self, party: str, base: str) -> Workspace:
        return Workspace(party, base, [base])

    async def aclose(self) -> None:
        return None

    def create_container(
        self,
        workspace: Workspace,
        command: Sequence[str],
        cwd: str,
        network: bool,
        env: Mapping[str, str] | None,
        options: Sequence[str] = (),
    ) -> str:
        container = f"doubleblind-{workspace.party}-{uuid.uuid4().hex[:12]}"
        create = ["create", "--name", container, "--workdir", cwd, *options]
        if not network:
            create += ["--network", "none"]
        for key, value in (env or {}).items():
            create += ["--env", f"{key}={value}"]
        self.checked([*create, self.image(workspace.head), *command])
        return container

    def execute(
        self,
        workspace: Workspace,
        command: str,
        persist: bool,
        network: bool,
        timeout_s: int,
        files: Mapping[str, bytes] | None,
        cwd: str,
        env: Mapping[str, str] | None,
    ) -> ExecResult:
        started = time.monotonic()
        container = self.create_container(workspace, ["sh", "-c", command], cwd, network, env)
        try:
            if files:
                self.copy_files(container, files)
            completed = self.docker_command(["start", "--attach", container], timeout_s)
            exit_code = int(self.checked(["inspect", "--format", "{{.State.ExitCode}}", container]))
            new_head = None
            if persist and exit_code == 0:
                new_head = self.checked(["commit", container])
                workspace.advance(new_head)
            return ExecResult(
                exit_code,
                completed.stdout,
                completed.stderr,
                False,
                time.monotonic() - started,
                0.0,
                new_head,
            )
        except subprocess.TimeoutExpired:
            self.docker_command(["kill", container])
            return ExecResult(-1, "", "timed out", True, time.monotonic() - started)
        finally:
            self.docker_command(["rm", "--force", container])

    def copy_files(self, container: str, files: Mapping[str, bytes]) -> None:
        with tempfile.TemporaryDirectory() as directory:
            staging = Path(directory)
            for path, content in files.items():
                target = staging / path.lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            self.checked(["cp", f"{staging}/.", f"{container}:/"])

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
    ) -> ExecResult:
        return await asyncio.to_thread(
            self.execute, workspace, command, persist, network, timeout_s, files, cwd, env
        )

    def launch(
        self,
        workspace: Workspace,
        command: str,
        network: bool,
        timeout_s: int,
        env: Mapping[str, str] | None,
        files: Mapping[str, bytes] | None,
    ) -> ServiceHandle:
        container = self.create_container(
            workspace,
            ["timeout", str(timeout_s), "sh", "-c", command],
            REPO_DIR,
            network,
            env,
            ["--add-host", HOST_ALIAS],
        )
        if files:
            self.copy_files(container, files)
        self.checked(["start", container])
        return ServiceHandle(container, workspace.party)

    async def start_service(
        self,
        workspace: Workspace,
        command: str,
        *,
        network: bool = True,
        timeout_s: int = SERVICE_TIMEOUT_S,
        env: Mapping[str, str] | None = None,
        files: Mapping[str, bytes] | None = None,
    ) -> ServiceHandle:
        return await asyncio.to_thread(
            self.launch, workspace, command, network, timeout_s, env, files
        )

    async def stop_service(self, handle: ServiceHandle) -> None:
        await asyncio.to_thread(self.docker_command, ["rm", "--force", handle.id])

    async def write_files(self, workspace: Workspace, files: Mapping[str, bytes]) -> ExecResult:
        result = await self.run(workspace, "true", persist=True, files=files, cwd="/")
        if not result.ok:
            raise SandboxError(f"writing files failed: {result.output()[:300]}")
        return result

    async def read_file(self, workspace: Workspace, path: str) -> bytes:
        result = await self.run(workspace, f"cat '{path}'", cwd="/")
        if not result.ok:
            raise SandboxError(f"cannot read {path}: {result.stderr[:200]}")
        return result.stdout.encode("utf-8")

    async def find_tag(self, tag: str) -> str | None:
        completed = self.docker_command(["image", "inspect", "--format", "{{.Id}}", tag])
        return completed.stdout.strip() if completed.returncode == 0 else None

    async def tag(self, workspace: Workspace, tag: str) -> None:
        self.checked(["tag", self.image(workspace.head), tag])
