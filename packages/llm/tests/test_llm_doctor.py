from pathlib import Path
from typing import Any

import pytest
from llm_helpers import ClientFactory, FakeTokenFactory, Reply, completion_body, error_body
from typer.testing import CliRunner

from doubleblind_llm import cli
from doubleblind_llm.capabilities import load_capabilities
from doubleblind_llm.client import LLMClient
from doubleblind_llm.doctor import Doctor

NANO = "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B"


def token_factory_behaviour(body: dict[str, Any]) -> Reply:
    if "tools" in body:
        calls = [{"id": "c1", "type": "function", "function": {"name": "echo", "arguments": "{}"}}]
        return 200, completion_body(None, tool_calls=calls)
    if "response_format" in body:
        return 200, completion_body('{"answer": "ok"}')
    if body.get("chat_template_kwargs", {}).get("enable_thinking") is False:
        return 200, completion_body("pong")
    if "reasoning_budget" in body:
        return 400, error_body("unknown field")
    return 200, completion_body("pong", reasoning_content="hmm")


@pytest.fixture
def fake_service(api: FakeTokenFactory) -> FakeTokenFactory:
    api.model_ids = [NANO]
    api.handler = token_factory_behaviour
    return api


async def test_doctor_detects_capabilities(
    make_client: ClientFactory, fake_service: FakeTokenFactory
) -> None:
    client = make_client()
    report = await Doctor(client).run(["nano", "super"])
    nano = report.models["nano"]
    assert nano.available
    assert nano.thinking.toggle == "chat_template_kwargs"
    assert nano.thinking.reasoning_fields == ["reasoning_content"]
    assert nano.thinking.reasoning_budget_param is False
    assert nano.native_tools
    assert nano.json_schema
    assert nano.latency_ms is not None
    assert nano.errors == ["reasoning_budget: rejected (400)"]
    assert report.models["super"].available is False
    assert client.ledger is not None
    assert {entry.role for entry in client.ledger.entries()} == {"doctor"}


def test_doctor_command_writes_report(
    make_client: ClientFactory,
    fake_service: FakeTokenFactory,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client = make_client()

    def fake_from_env(**_: object) -> LLMClient:
        return client

    monkeypatch.setattr(cli.LLMClient, "from_env", staticmethod(fake_from_env))
    target = tmp_path / "capabilities.json"
    result = CliRunner().invoke(cli.app, ["doctor", "--model", "nano", "--output", str(target)])
    assert result.exit_code == 0, result.output
    report = load_capabilities(target)
    assert report is not None
    assert list(report.models) == ["nano"]
    assert "Doctor spent $" in result.output
