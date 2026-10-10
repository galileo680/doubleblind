import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from doubleblind_sandbox.docker import DockerSandboxProvider, docker_available
from doubleblind_sandbox.images import (
    BasePlan,
    base_tag,
    content_hash,
    ensure_base_image,
    repo_files,
)
from doubleblind_sandbox.local import LocalSandboxProvider
from doubleblind_sandbox.provider import (
    ExecResult,
    ProbeResult,
    SandboxError,
    Workspace,
    branch,
    repo_path,
)

PYTHON = f'"{sys.executable}"'


async def local_workspace(tmp_path: Path) -> tuple[LocalSandboxProvider, Workspace]:
    source = tmp_path / "repo"
    source.mkdir()
    (source / "app.py").write_text("print('hello')\n", encoding="utf-8")
    sandbox = LocalSandboxProvider(tmp_path / "sandbox")
    head = await sandbox.import_directory(source, tag="base")
    return sandbox, await sandbox.open_workspace("spokes", head)


async def test_local_runs_commands_in_the_current_version(tmp_path: Path) -> None:
    sandbox, workspace = await local_workspace(tmp_path)

    result = await sandbox.run(workspace, f"{PYTHON} app.py")

    assert result.ok
    assert result.stdout.strip() == "hello"
    assert workspace.history == [workspace.head]


async def test_local_versions_and_branches_are_isolated(tmp_path: Path) -> None:
    sandbox, workspace = await local_workspace(tmp_path)
    other = branch(workspace)

    await sandbox.write_files(workspace, {repo_path("app.py"): b"print('changed')\n"})
    changed = await sandbox.run(workspace, f"{PYTHON} app.py")
    original = await sandbox.run(other, f"{PYTHON} app.py")

    assert changed.stdout.strip() == "changed"
    assert original.stdout.strip() == "hello"
    assert len(workspace.history) == 2
    assert await sandbox.read_file(workspace, repo_path("app.py")) == b"print('changed')\n"


async def test_local_failed_or_slow_commands_keep_the_head(tmp_path: Path) -> None:
    sandbox, workspace = await local_workspace(tmp_path)
    head = workspace.head

    failed = await sandbox.run(workspace, f'{PYTHON} -c "raise SystemExit(4)"', persist=True)
    slow = await sandbox.run(
        workspace, f'{PYTHON} -c "import time; time.sleep(5)"', persist=True, timeout_s=1
    )

    assert failed.exit_code == 4
    assert slow.timed_out
    assert workspace.head == head


async def test_local_tags_and_missing_files(tmp_path: Path) -> None:
    sandbox, workspace = await local_workspace(tmp_path)
    tagged = await sandbox.open_workspace("spokes", "tag:base")
    await sandbox.tag(tagged, "copy")

    assert await sandbox.find_tag("copy") == workspace.head
    assert await sandbox.find_tag("missing") is None
    with pytest.raises(SandboxError, match="no such file"):
        await sandbox.read_file(workspace, repo_path("missing.py"))
    with pytest.raises(SandboxError, match="no image with tag"):
        await sandbox.open_workspace("spokes", "tag:missing")
    await sandbox.aclose()


class FakeDocker:
    def __init__(self, exit_code: str = "0") -> None:
        self.commands: list[list[str]] = []
        self.exit_code = exit_code

    def __call__(
        self, arguments: Sequence[str], timeout_s: float
    ) -> subprocess.CompletedProcess[str]:
        self.commands.append(list(arguments))
        verb = arguments[1]
        stdout = {"inspect": self.exit_code, "commit": "sha256:new", "start": "done"}.get(verb, "")
        return subprocess.CompletedProcess(list(arguments), 0, stdout, "")


async def test_docker_runs_without_network_and_commits_on_persist() -> None:
    docker = FakeDocker()
    sandbox = DockerSandboxProvider(runner=docker)
    workspace = await sandbox.open_workspace("spokes", "tag:doubleblind-spokes-base")

    result = await sandbox.run(
        workspace, "pytest -q", persist=True, files={"/workspace/repo/a.py": b"x"}, env={"A": "1"}
    )

    create = docker.commands[0]
    assert create[:2] == ["docker", "create"]
    assert create[create.index("--network") : create.index("--network") + 2] == [
        "--network",
        "none",
    ]
    assert "A=1" in create
    assert create[-4:] == ["doubleblind-spokes-base", "sh", "-c", "pytest -q"]
    assert any(command[1] == "cp" for command in docker.commands)
    assert docker.commands[-2][:2] == ["docker", "commit"]
    assert docker.commands[-1][:3] == ["docker", "rm", "--force"]
    assert result.ok and result.stdout == "done"
    assert workspace.head == "sha256:new"


async def test_docker_network_on_and_failures() -> None:
    docker = FakeDocker(exit_code="3")
    sandbox = DockerSandboxProvider(runner=docker)
    workspace = await sandbox.open_workspace("spokes", "image")

    result = await sandbox.run(workspace, "false", persist=True, network=True)

    assert "--network" not in docker.commands[0]
    assert result.exit_code == 3
    assert workspace.head == "image"
    assert not any(command[1] == "commit" for command in docker.commands)


def test_docker_is_reported_unavailable_without_a_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shutil.which", lambda name: None)

    assert docker_available() is False


def test_repo_files_skip_dependencies_and_caches(tmp_path: Path) -> None:
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.js").write_text("x", encoding="utf-8")
    (tmp_path / "node_modules" / "lib").mkdir(parents=True)
    (tmp_path / "node_modules" / "lib" / "b.js").write_text("y", encoding="utf-8")
    (tmp_path / "spokes.db").write_text("z", encoding="utf-8")

    files = repo_files(tmp_path)

    assert list(files) == ["/workspace/repo/src/a.js"]
    assert content_hash(files) == content_hash(dict(reversed(list(files.items()))))
    assert content_hash(files) != content_hash(files, ["other"])
    assert base_tag("spokes", "abcdef1234567890") == "doubleblind-spokes-base-abcdef123456"


class RecordingProvider:
    name = "recording"

    def __init__(self, exit_code: int = 0) -> None:
        self.tags: dict[str, str] = {}
        self.runs: list[dict[str, object]] = []
        self.exit_code = exit_code

    async def open_workspace(self, party: str, base: str) -> Workspace:
        return Workspace(party, base, [base])

    async def run(self, workspace: Workspace, command: str, **options: object) -> ExecResult:
        self.runs.append({"command": command, **options})
        if self.exit_code == 0:
            workspace.advance("built-image")
        return ExecResult(self.exit_code, "built", "", cost_usd=0.1)

    async def tag(self, workspace: Workspace, tag: str) -> None:
        self.tags[tag] = workspace.head

    async def find_tag(self, tag: str) -> str | None:
        return self.tags.get(tag)


async def test_base_image_is_built_once_per_content(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    recipe = tmp_path / "recipe.sh"
    recipe.write_text("echo build\n", encoding="utf-8")
    plan = BasePlan("spokes", repo, recipe)
    sandbox = RecordingProvider()

    first = await ensure_base_image(sandbox, plan, {"c.whl": b"wheel"}, "contract-1")
    second = await ensure_base_image(sandbox, plan, {"c.whl": b"other"}, "contract-1")
    changed = await ensure_base_image(sandbox, plan, {"c.whl": b"wheel"}, "contract-2")

    assert first.built and not second.built and changed.built
    assert first.tag == second.tag != changed.tag
    run = sandbox.runs[0]
    assert run["network"] is True and run["persist"] is True
    files = run["files"]
    assert isinstance(files, dict)
    assert set(files) == {
        "/workspace/repo/app.py",
        "/opt/doubleblind/recipe.sh",
        "/opt/doubleblind/wheels/c.whl",
    }


async def test_failed_base_build_raises_with_the_log(tmp_path: Path) -> None:
    plan = BasePlan("spokes", tmp_path, tmp_path / "recipe.sh")
    (tmp_path / "recipe.sh").write_text("exit 1\n", encoding="utf-8")

    with pytest.raises(SandboxError, match="base image failed"):
        await ensure_base_image(RecordingProvider(exit_code=1), plan, {}, "x")


def test_probe_result_serializes() -> None:
    probe = ProbeResult("dns", "error", "error", "passed")

    assert probe.to_dict()["status"] == "passed"
