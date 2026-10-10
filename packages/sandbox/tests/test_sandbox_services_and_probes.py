import asyncio
import base64
import sys
import time
from pathlib import Path
from typing import Any, cast

import pytest
from test_sandbox_local_and_docker import FakeDocker

from doubleblind_sandbox.docker import DockerSandboxProvider
from doubleblind_sandbox.isolation_probe import (
    SandboxIsolationProbe,
    find_address,
    probe_credentials,
    probe_listing,
    probe_models,
    probe_no_route,
)
from doubleblind_sandbox.local import LocalSandboxProvider
from doubleblind_sandbox.provider import (
    REPO_DIR,
    ExecResult,
    ProbeResult,
    ServiceSandbox,
    Workspace,
    repo_path,
)

PYTHON = f'"{sys.executable}"'


async def shared_sandbox(tmp_path: Path) -> tuple[LocalSandboxProvider, Workspace, Path]:
    dependencies = tmp_path / "deps"
    (dependencies / "lib").mkdir(parents=True)
    (dependencies / "lib" / "index.js").write_text("ok", encoding="utf-8")
    source = tmp_path / "repo"
    source.mkdir()
    (source / "app.py").write_text("print('hi')\n", encoding="utf-8")
    sandbox = LocalSandboxProvider(tmp_path / "sandbox", {f"{REPO_DIR}/node_modules": dependencies})
    head = await sandbox.import_directory(source)
    return sandbox, await sandbox.open_workspace("parcelly", head), dependencies


async def test_shared_directories_are_linked_not_copied(tmp_path: Path) -> None:
    sandbox, workspace, dependencies = await shared_sandbox(tmp_path)

    await sandbox.write_files(workspace, {repo_path("b.py"): b"x"})
    linked = await sandbox.read_file(workspace, repo_path("node_modules/lib/index.js"))
    sandbox.remove_all()

    assert linked == b"ok"
    assert len(workspace.history) == 2
    assert (dependencies / "lib" / "index.js").read_text(encoding="utf-8") == "ok"
    assert not (tmp_path / "sandbox").exists()


async def test_local_service_runs_until_stopped(tmp_path: Path) -> None:
    sandbox, workspace, _ = await shared_sandbox(tmp_path)
    command = (
        f"{PYTHON} -c \"import time, pathlib; pathlib.Path('started').write_text('yes'); "
        'time.sleep(120)"'
    )

    handle = await sandbox.start_service(workspace, command, env={"A": "1"})
    process, version = sandbox.services[handle.id]
    deadline = time.monotonic() + 30
    while not (version / REPO_DIR.strip("/") / "started").exists():
        assert time.monotonic() < deadline
        await asyncio.sleep(0.1)
    await sandbox.stop_service(handle)

    assert process.poll() is not None
    assert not version.exists()
    assert await sandbox.stop_service(handle) == 0.0
    await sandbox.aclose()


async def test_docker_service_has_network_and_a_host_alias() -> None:
    docker = FakeDocker()
    sandbox = DockerSandboxProvider(runner=docker)
    workspace = await sandbox.open_workspace("spokes", "tag:base")

    handle = await sandbox.start_service(
        workspace, "python3 relay.py", env={"T": "x"}, files={"/workspace/repo/relay.py": b"x"}
    )
    await sandbox.stop_service(handle)

    create = docker.commands[0]
    assert "--network" not in create
    assert "host.docker.internal:host-gateway" in create
    assert create[-6:] == ["base", "timeout", "900", "sh", "-c", "python3 relay.py"]
    assert [command[1] for command in docker.commands] == ["create", "cp", "start", "rm"]
    assert docker.commands[-1][-1] == handle.id


class ListingSandbox:
    def __init__(self, stdout: str) -> None:
        self.stdout = stdout
        self.commands: list[tuple[str, str]] = []

    async def run(self, workspace: Workspace, command: str, **options: Any) -> ExecResult:
        self.commands.append((command, str(options.get("cwd"))))
        return ExecResult(0, self.stdout, "")


async def test_listing_probe_accepts_only_the_repo() -> None:
    workspace = Workspace("spokes", "tag:base", ["tag:base"])
    clean, leaky = ListingSandbox("repo\n"), ListingSandbox("parcelly\nrepo\n")

    passed = await probe_listing(cast(ServiceSandbox, clean), workspace)
    failed = await probe_listing(cast(ServiceSandbox, leaky), workspace)

    assert passed.status == "passed" and passed.actual == "repo"
    assert failed.status == "failed" and failed.actual == "parcelly repo"
    assert clean.commands == [("ls /workspace", "/")]


async def test_route_and_credential_probes_skip_without_token_factory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    sandbox, workspace, _ = await shared_sandbox(tmp_path)
    monkeypatch.delenv("SPOKES_SANDBOX_API_KEY", raising=False)

    route = await probe_no_route(sandbox, workspace, workspace)
    credentials = await probe_credentials("doubleblind-parcelly-base-x")
    models = await probe_models(None)

    assert route.status == credentials.status == "skipped"
    assert models.status == "failed"


def test_listener_address_is_found_in_plain_and_base64_output() -> None:
    encoded = base64.b64encode(b"PROBE_IP 10.9.0.7\n").decode()

    assert find_address("PROBE_IP 10.9.0.118\n") == "10.9.0.118"
    assert find_address(encoded) == "10.9.0.7"
    assert find_address("starting") is None


async def test_probe_runs_every_check(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def fake(name: str) -> ProbeResult:
        calls.append(name)
        return ProbeResult(name, "x", "x", "passed")

    monkeypatch.setattr("doubleblind_sandbox.isolation_probe.probe_dns", lambda *_: fake("dns"))
    monkeypatch.setattr(
        "doubleblind_sandbox.isolation_probe.probe_listing", lambda *_: fake("listing")
    )
    monkeypatch.setattr(
        "doubleblind_sandbox.isolation_probe.probe_credentials", lambda *_: fake("credentials")
    )
    monkeypatch.setattr(
        "doubleblind_sandbox.isolation_probe.probe_no_route", lambda *_: fake("route")
    )
    monkeypatch.setattr(
        "doubleblind_sandbox.isolation_probe.probe_models", lambda *_: fake("models")
    )
    probe = SandboxIsolationProbe(
        lambda: DockerSandboxProvider(runner=FakeDocker()), {"spokes": "a", "parcelly": "b"}
    )

    results = await probe.run()

    assert [result.name for result in results] == [
        "dns",
        "listing",
        "credentials",
        "route",
        "models",
    ]
    assert sorted(calls) == ["credentials", "dns", "listing", "models", "route"]
