from typing import Any

import pytest
from broker_helpers import PRESETS_DIR

from doubleblind_broker.autopilot import (
    DefaultOptionSimulator,
    LLMHumanSimulator,
    autopilot_answer,
    fallback_answer,
)
from doubleblind_broker.presets import PresetNotFoundError, load_preset

PRESET = load_preset(PRESETS_DIR, "spokes-parcelly")


class FakeResponse:
    def __init__(self, parsed: dict[str, Any] | None) -> None:
        self.parsed = parsed


class FakeClient:
    def __init__(self, parsed: dict[str, Any] | None = None, fail: bool = False) -> None:
        self.parsed = parsed
        self.fail = fail
        self.requests: list[Any] = []

    async def complete(self, request: Any) -> FakeResponse:
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("model unavailable")
        return FakeResponse(self.parsed)


def test_preset_answers_match_keywords() -> None:
    assert PRESET.preset_answer("Czy dodać status RETURNED?") == "tak"
    assert PRESET.preset_answer("Co z adresami nieparsowalnymi?") == (
        "tak, oznacz do ręcznej weryfikacji i nie wysyłaj"
    )
    assert PRESET.preset_answer("Jaki kolor etykiety?") is None
    assert "600100200" in PRESET.allowlist().phones


def test_unknown_preset_is_reported() -> None:
    with pytest.raises(PresetNotFoundError):
        load_preset(PRESETS_DIR, "missing")


async def test_autopilot_prefers_the_preset_then_the_simulator() -> None:
    simulator = DefaultOptionSimulator()

    from_preset = await autopilot_answer(
        PRESET, simulator, "Status returned?", ["x"], None, "consumer"
    )
    from_default = await autopilot_answer(
        PRESET, simulator, "Kolor?", ["zielony"], "niebieski", "provider"
    )

    assert from_preset == "tak"
    assert from_default == "niebieski"


async def test_llm_simulator_uses_the_persona_and_the_model_answer() -> None:
    client = FakeClient({"answer": "zielony"})

    answer = await LLMHumanSimulator(client).answer(
        "Kolor?", ["zielony", "czerwony"], None, "careful owner"
    )

    assert answer == "zielony"
    assert "careful owner" in client.requests[0].messages[0]["content"]


async def test_llm_simulator_falls_back_to_the_default() -> None:
    failing = await LLMHumanSimulator(FakeClient(fail=True)).answer(
        "Kolor?", ["zielony"], "czerwony", "owner"
    )
    empty = await LLMHumanSimulator(FakeClient({})).answer("Kolor?", ["zielony"], None, "owner")

    assert failing == "czerwony"
    assert empty == "zielony"
    assert fallback_answer([], None) == "yes"
