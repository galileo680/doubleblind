import asyncio
import os
import shutil
import signal
import subprocess
import sys
import time
import uuid
from collections.abc import Mapping
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

LOCAL_PREFIX = "local:"
NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)


def link_directory(source: Path, link: Path) -> None:
    link.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        import _winapi

        _winapi.CreateJunction(str(source), str(link))
    else:
        link.symlink_to(source, target_is_directory=True)


def unlink_directory(link: Path) -> None:
    if not os.path.lexists(link):
        return
    if os.name == "nt":
        os.rmdir(link)
    else:
        link.unlink()


def local_env(env: Mapping[str, str] | None, version: Path) -> dict[str, str]:
    merged = {**os.environ, **(env or {}), "SANDBOX_ROOT": str(version)}
    merged["PATH"] = os.pathsep.join([str(Path(sys.executable).parent), merged.get("PATH", "")])
    return merged


def stop_process_tree(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(process.pid)], capture_output=True, check=False
        )
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


class LocalSandboxProvider:
    name = "local"
    spent_usd = 0.0
    python = f'"{sys.executable}"'

    def __init__(self, root: Path, shared: Mapping[str, Path] | None = None) -> None:
        self.root = root
        self.shared = {path.strip("/"): Path(source) for path, source in (shared or {}).items()}
        self.tags: dict[str, str] = {}
        self.services: dict[str, tuple[subprocess.Popen[bytes], Path]] = {}

    def resolve(self, head: str) -> str:
        if not head.startswith("tag:"):
            return head
        tagged = self.tags.get(head.removeprefix("tag:"))
        if tagged is None:
            raise SandboxError(f"no image with tag {head.removeprefix('tag:')}")
        return tagged

    def version_dir(self, head: str) -> Path:
        return self.root / self.resolve(head).removeprefix(LOCAL_PREFIX)

    def local_path(self, version: Path, sandbox_path: str) -> Path:
        return version / sandbox_path.lstrip("/")

    def link_shared(self, version: Path) -> None:
        for path, source in self.shared.items():
            link = version / path
            if not os.path.lexists(link):
                link_directory(source, link)

    def copy_tree(self, source: Path, target: Path, prefix: str) -> None:
        def ignore(directory: str, names: list[str]) -> list[str]:
            relative = Path(directory).relative_to(source).as_posix()
            base = prefix if relative == "." else f"{prefix}/{relative}"
            return [name for name in names if f"{base}/{name}".strip("/") in self.shared]

        shutil.copytree(source, target, symlinks=True, ignore=ignore)

    def remove_version(self, version: Path) -> None:
        for path in self.shared:
            unlink_directory(version / path)
        shutil.rmtree(version, ignore_errors=True)

    def remove_all(self) -> None:
        if self.root.is_dir():
            for version in self.root.iterdir():
                self.remove_version(version)
        shutil.rmtree(self.root, ignore_errors=True)

    async def import_directory(self, source: Path, tag: str | None = None) -> str:
        head = f"{LOCAL_PREFIX}{uuid.uuid4().hex}"
        version = self.version_dir(head)
        target = self.local_path(version, REPO_DIR)
        await asyncio.to_thread(self.copy_tree, source, target, REPO_DIR.strip("/"))
        self.link_shared(version)
        if tag:
            self.tags[tag] = head
        return head

    async def open_workspace(self, party: str, base: str) -> Workspace:
        self.version_dir(base)
        return Workspace(party, base, [base])

    async def aclose(self) -> None:
        for handle in list(self.services):
            await self.stop_service(ServiceHandle(handle, ""))

    def snapshot(self, head: str) -> tuple[str, Path]:
        new_head = f"{LOCAL_PREFIX}{uuid.uuid4().hex}"
        target = self.version_dir(new_head)
        self.copy_tree(self.version_dir(head), target, "")
        self.link_shared(target)
        return new_head, target

    def prepare(
        self, workspace: Workspace, files: Mapping[str, bytes] | None, cwd: str
    ) -> tuple[str, Path, Path]:
        new_head, version = self.snapshot(workspace.head)
        for path, content in (files or {}).items():
            target = self.local_path(version, path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        workdir = self.local_path(version, cwd)
        workdir.mkdir(parents=True, exist_ok=True)
        return new_head, version, workdir

    def execute(
        self,
        workspace: Workspace,
        command: str,
        persist: bool,
        timeout_s: int,
        files: Mapping[str, bytes] | None,
        cwd: str,
        env: Mapping[str, str] | None,
    ) -> ExecResult:
        started = time.monotonic()
        new_head, version, workdir = self.prepare(workspace, files, cwd)
        try:
            completed = subprocess.run(
                command,
                shell=True,
                cwd=workdir,
                env=local_env(env, version),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_s,
                check=False,
            )
            exit_code, stdout, stderr, timed_out = (
                completed.returncode,
                completed.stdout,
                completed.stderr,
                False,
            )
        except subprocess.TimeoutExpired as error:
            exit_code, stdout, stderr, timed_out = -1, str(error.stdout or ""), "timed out", True
        result = ExecResult(
            exit_code,
            stdout,
            stderr,
            timed_out,
            time.monotonic() - started,
            0.0,
            new_head if persist else None,
        )
        if persist and result.ok:
            workspace.advance(new_head)
        else:
            self.remove_version(version)
        return result

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
            self.execute, workspace, command, persist, timeout_s, files, cwd, env
        )

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
        _, version, workdir = await asyncio.to_thread(self.prepare, workspace, files, REPO_DIR)
        log = (version / "service.log").open("wb")
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=workdir,
            env=local_env(env, version),
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=NEW_PROCESS_GROUP,
            start_new_session=sys.platform != "win32",
        )
        log.close()
        handle = ServiceHandle(uuid.uuid4().hex, workspace.party)
        self.services[handle.id] = (process, version)
        return handle

    async def stop_service(self, handle: ServiceHandle) -> None:
        service = self.services.pop(handle.id, None)
        if service is None:
            return
        process, version = service
        await asyncio.to_thread(stop_process_tree, process)
        await asyncio.to_thread(self.remove_version, version)

    async def write_files(self, workspace: Workspace, files: Mapping[str, bytes]) -> ExecResult:
        result = await self.run(workspace, "exit 0", persist=True, files=files, cwd="/")
        if not result.ok:
            raise SandboxError(f"writing files failed: {result.output()[:300]}")
        return result

    async def read_file(self, workspace: Workspace, path: str) -> bytes:
        target = self.local_path(self.version_dir(workspace.head), path)
        if not target.is_file():
            raise SandboxError(f"cannot read {path}: no such file")
        return target.read_bytes()

    async def find_tag(self, tag: str) -> str | None:
        return self.tags.get(tag)

    async def tag(self, workspace: Workspace, tag: str) -> None:
        self.tags[tag] = self.resolve(workspace.head)
