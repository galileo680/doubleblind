from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
REQUIRED_ROLES = {"psm", "brief", "negotiator", "coder", "triage", "guard", "repair", "human_sim"}
REQUIRED_ENV_KEYS = {
    "NEBIUS_API_KEY",
    "NEBIUS_PROJECT_ID",
    "DOUBLEBLIND_LLM_MODE",
    "DOUBLEBLIND_SPEND_LIMIT_USD",
    "DOUBLEBLIND_SANDBOX",
    "DOUBLEBLIND_MAX_ROUNDS",
}


def load_config(name: str) -> Any:
    return yaml.safe_load((ROOT / "config" / name).read_text(encoding="utf-8"))


def test_models_config_points_to_token_factory() -> None:
    models = load_config("models.yaml")
    assert models["base_url"] == "https://api.tokenfactory.nebius.com/v1/"


def test_every_required_role_is_configured() -> None:
    roles = load_config("models.yaml")["roles"]
    assert set(roles) >= REQUIRED_ROLES


def test_every_role_uses_a_defined_model_with_valid_thinking_mode() -> None:
    models = load_config("models.yaml")
    for role, settings in models["roles"].items():
        assert settings["model"] in models["models"], role
        assert settings["thinking"] in {"on", "off"}, role


def test_every_model_has_pricing() -> None:
    model_ids = {model["id"] for model in load_config("models.yaml")["models"].values()}
    pricing = load_config("pricing.yaml")
    assert model_ids == set(pricing)
    for prices in pricing.values():
        assert prices["input_per_m"] > 0
        assert prices["output_per_m"] > 0


def test_env_example_lists_required_keys_without_values_for_secrets() -> None:
    entries = dict(
        line.split("=", 1)
        for line in (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
        if "=" in line
    )
    assert set(entries) >= REQUIRED_ENV_KEYS
    assert entries["NEBIUS_API_KEY"] == ""
    assert float(entries["DOUBLEBLIND_SPEND_LIMIT_USD"]) <= 30
