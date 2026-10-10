from pathlib import Path

import pytest
from sandbox_fakes import FakeSandboxApi

from doubleblind_llm.budget import Ledger
from doubleblind_llm.errors import BudgetExceededError
from doubleblind_sandbox.costs import LedgerCosts
from doubleblind_sandbox.provider import SandboxError, branch
from doubleblind_sandbox.tokenfactory import TokenFactorySandboxProvider, decode_stream


async def no_sleep(_: float) -> None:
    return None


def provider(api: FakeSandboxApi, costs: LedgerCosts | None = None) -> TokenFactorySandboxProvider:
    return TokenFactorySandboxProvider(
        "key", "project", costs, transport=api.transport(), sleep=no_sleep
    )


async def test_run_sends_a_network_free_disposable_instance_by_default() -> None:
    api = FakeSandboxApi()
    sandbox = provider(api)
    workspace = await sandbox.open_workspace("spokes", "tag:base")

    result = await sandbox.run(workspace, "pytest -q", env={"A": "1"})

    body = api.instances[0]
    assert body["image"] == "tag:base"
    assert body["networking"] == {"enabled": False}
    assert body["disposable"] is True
    assert body["shell"] is True
    assert body["cwd"] == "/workspace/repo"
    assert body["env"] == {"A": "1"}
    assert result.ok
    assert result.stdout == "ok"
    assert result.cost_usd == 0.0012
    assert result.new_head is None
    assert workspace.head == "tag:base"
    await sandbox.aclose()


async def test_persisted_run_moves_the_workspace_head() -> None:
    api = FakeSandboxApi()
    sandbox = provider(api)
    workspace = await sandbox.open_workspace("spokes", "tag:base")
    other = branch(workspace)

    result = await sandbox.run(workspace, "git commit", persist=True, network=True)

    assert api.instances[0]["disposable"] is False
    assert api.instances[0]["networking"] == {"enabled": True}
    assert result.new_head == "image-op-1"
    assert workspace.head == "image-op-1"
    assert workspace.history == ["tag:base", "image-op-1"]
    assert other.head == "tag:base"


async def test_failed_command_does_not_move_the_head() -> None:
    api = FakeSandboxApi()
    api.exit_code = 1
    sandbox = provider(api)
    workspace = await sandbox.open_workspace("spokes", "tag:base")

    result = await sandbox.run(workspace, "false", persist=True)

    assert not result.ok
    assert workspace.head == "tag:base"


async def test_files_are_uploaded_once_and_referenced_by_uuid() -> None:
    api = FakeSandboxApi()
    sandbox = provider(api)
    workspace = await sandbox.open_workspace("spokes", "tag:base")

    await sandbox.write_files(workspace, {"/workspace/repo/a.py": b"x = 1\n"})
    await sandbox.run(workspace, "cat a.py", files={"/workspace/repo/b.py": b"x = 1\n"})

    assert len(api.files) == 1
    spec = api.instances[0]["files"]["/workspace/repo/a.py"]
    assert spec == {"uuid": "file-1", "mode": "0644", "uid": 0, "gid": 0}
    assert api.instances[1]["files"]["/workspace/repo/b.py"]["uuid"] == "file-1"


async def test_base64_output_is_decoded() -> None:
    api = FakeSandboxApi()
    api.encoding = "base64"
    api.stdout = "zażółć\n"
    sandbox = provider(api)
    workspace = await sandbox.open_workspace("spokes", "tag:base")

    result = await sandbox.run(workspace, "echo")

    assert result.stdout == "zażółć\n"
    assert decode_stream(None) == ""


async def test_failed_operation_is_reported() -> None:
    api = FakeSandboxApi()
    api.status = "FAILED"
    sandbox = provider(api)
    workspace = await sandbox.open_workspace("spokes", "tag:base")

    result = await sandbox.run(workspace, "boom")

    assert result.exit_code == -1
    assert "worker crashed" in result.stderr


async def test_costs_go_to_the_ledger_and_the_budget_is_checked(tmp_path: Path) -> None:
    api = FakeSandboxApi()
    ledger = Ledger(tmp_path / "ledger.jsonl")
    sandbox = provider(api, LedgerCosts(ledger, limit_usd=1.0, session_id="ses_1"))
    workspace = await sandbox.open_workspace("parcelly", "tag:base")

    await sandbox.run(workspace, "npm test")
    entry = ledger.entries()[0]
    broke = provider(api, LedgerCosts(ledger, limit_usd=0.1))

    assert entry.role == "sandbox:parcelly"
    assert entry.model == "token-factory-sandboxes"
    assert entry.cost_usd == 0.0012
    assert entry.session_id == "ses_1"
    with pytest.raises(BudgetExceededError):
        await broke.run(workspace, "npm test")
    assert len(api.instances) == 1


async def test_tags_are_found_set_and_resolved() -> None:
    api = FakeSandboxApi()
    sandbox = provider(api)
    workspace = await sandbox.open_workspace("spokes", "tag:base")
    await sandbox.run(workspace, "true", persist=True)

    await sandbox.tag(workspace, "doubleblind-spokes-base-abc")

    assert await sandbox.find_tag("doubleblind-spokes-base-abc") == "image-op-1"
    assert await sandbox.find_tag("missing") is None
    with pytest.raises(SandboxError, match="no image with tag"):
        await sandbox.resolve("tag:missing")


async def test_read_file_downloads_and_falls_back_to_cat() -> None:
    api = FakeSandboxApi()
    api.tags["base"] = "image-base"
    api.downloads[("image-base", "/workspace/repo/a.py")] = b"print(1)\n"
    sandbox = provider(api)
    workspace = await sandbox.open_workspace("spokes", "tag:base")

    downloaded = await sandbox.read_file(workspace, "/workspace/repo/a.py")
    api.stdout = "cHJpbnQoMikK"
    via_cat = await sandbox.read_file(workspace, "/workspace/repo/b.py")

    assert downloaded == b"print(1)\n"
    assert via_cat == b"print(2)\n"
    assert api.instances[-1]["command"] == "base64 -w0 '/workspace/repo/b.py'"


async def test_import_reuses_an_existing_tag_and_reports_failures() -> None:
    api = FakeSandboxApi()
    sandbox = provider(api)

    imported = await sandbox.import_image("docker://node:24-slim", "node:24-slim")
    again = await sandbox.import_image("docker://node:24-slim", "node:24-slim")
    api.import_status = "FAILED"

    assert imported == again == "image-imported"
    with pytest.raises(SandboxError, match="registry unreachable"):
        await sandbox.import_image("docker://broken", "broken")


async def test_slow_operations_are_cancelled() -> None:
    api = FakeSandboxApi()
    api.pending_polls = 10_000
    sandbox = provider(api)

    with pytest.raises(SandboxError, match="did not finish"):
        await sandbox.wait("op-x", timeout_s=0)

    assert api.deleted == ["op-x"]


async def test_service_can_be_stopped_and_its_cost_recorded(tmp_path: Path) -> None:
    api = FakeSandboxApi()
    ledger = Ledger(tmp_path / "ledger.jsonl")
    sandbox = provider(api, LedgerCosts(ledger, limit_usd=1.0))
    workspace = await sandbox.open_workspace("spokes", "tag:base")

    handle = await sandbox.start_service(workspace, "python -m http.server")
    cost = await sandbox.stop_service(handle)

    assert api.instances[0]["networking"] == {"enabled": True}
    assert api.deleted == ["op-1"]
    assert cost == 0.0012
    assert ledger.entries()[0].note == "service"


def test_from_env_needs_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NEBIUS_API_KEY", raising=False)
    monkeypatch.delenv("SANDBOX_API_KEY", raising=False)

    with pytest.raises(SandboxError, match="NEBIUS_API_KEY"):
        TokenFactorySandboxProvider.from_env()

    monkeypatch.setenv("SPOKES_SANDBOX_API_KEY", "k")
    monkeypatch.setenv("SPOKES_SANDBOX_PROJECT_ID", "p")
    assert TokenFactorySandboxProvider.from_env(prefix="SPOKES_").name == "tokenfactory"
