from pathlib import Path

import pytest
from llm_helpers import ClientFactory, FakeTokenFactory, capability_report

from doubleblind_llm.budget import Ledger
from doubleblind_llm.capabilities import CapabilityReport
from doubleblind_llm.cassette import Cassette
from doubleblind_llm.client import CallListener, LLMClient
from doubleblind_llm.settings import LLMSettings, Mode, load_settings


@pytest.fixture
def settings() -> LLMSettings:
    return load_settings()


@pytest.fixture
def ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.jsonl")


@pytest.fixture
def api() -> FakeTokenFactory:
    return FakeTokenFactory()


@pytest.fixture
def make_client(
    tmp_path: Path, settings: LLMSettings, ledger: Ledger, api: FakeTokenFactory
) -> ClientFactory:
    def factory(
        mode: Mode = "live",
        *,
        capabilities: CapabilityReport | None = None,
        limit_usd: float = 25.0,
        api_key: str | None = "test-key",
        on_call: CallListener | None = None,
    ) -> LLMClient:
        return LLMClient(
            settings,
            mode=mode,
            api_key=api_key,
            capabilities=capabilities or capability_report(settings),
            cassette=Cassette(tmp_path / "cassette.jsonl") if mode != "live" else None,
            ledger=ledger,
            spend_limit_usd=limit_usd,
            on_call=on_call,
            http_client=api.client(),
            retry_wait_seconds=0,
        )

    return factory
