from pathlib import Path

import pytest
from llm_helpers import ROOT

from doubleblind_llm.errors import ConfigurationError
from doubleblind_llm.settings import LLMSettings, load_dotenv, load_settings, mode_from_env


def test_settings_resolve_roles_to_models(settings: LLMSettings) -> None:
    assert settings.base_url == "https://api.tokenfactory.nebius.com/v1/"
    assert settings.model(settings.role("negotiator").model).id == (
        "nvidia/Nemotron-3-Ultra-550b-a55b"
    )
    assert settings.price_for("nvidia/nemotron-3-super-120b-a12b").output_per_m == 0.9


def test_unknown_role_and_model_raise(settings: LLMSettings) -> None:
    with pytest.raises(ConfigurationError):
        settings.role("missing")
    with pytest.raises(ConfigurationError):
        settings.price_for("unknown/model")


def test_role_with_unknown_model_alias_is_rejected(tmp_path: Path) -> None:
    models = (ROOT / "config" / "models.yaml").read_text(encoding="utf-8")
    (tmp_path / "models.yaml").write_text(
        models.replace("model: lightning", "model: missing"), encoding="utf-8"
    )
    pricing = (ROOT / "config" / "pricing.yaml").read_text(encoding="utf-8")
    (tmp_path / "pricing.yaml").write_text(pricing, encoding="utf-8")
    with pytest.raises(ConfigurationError, match="unknown model alias"):
        load_settings(tmp_path)


def test_mode_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOUBLEBLIND_LLM_MODE", raising=False)
    assert mode_from_env() == "live"
    monkeypatch.setenv("DOUBLEBLIND_LLM_MODE", "Replay")
    assert mode_from_env() == "replay"
    monkeypatch.setenv("DOUBLEBLIND_LLM_MODE", "fake")
    with pytest.raises(ConfigurationError):
        mode_from_env()


def test_dotenv_does_not_override_existing_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text('DOUBLEBLIND_TEST_A="from-file"\nDOUBLEBLIND_TEST_B=file\n', "utf-8")
    monkeypatch.setenv("DOUBLEBLIND_TEST_B", "existing")
    monkeypatch.delenv("DOUBLEBLIND_TEST_A", raising=False)
    load_dotenv(env_file)
    import os

    assert os.environ["DOUBLEBLIND_TEST_A"] == "from-file"
    assert os.environ["DOUBLEBLIND_TEST_B"] == "existing"
    monkeypatch.delenv("DOUBLEBLIND_TEST_A")
