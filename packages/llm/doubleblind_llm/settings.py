import os
from pathlib import Path
from typing import Any, Literal, cast

import yaml
from pydantic import BaseModel, ConfigDict, Field

from doubleblind_llm.errors import ConfigurationError

CONFIG_DIR_ENV = "DOUBLEBLIND_CONFIG_DIR"
MODE_ENV = "DOUBLEBLIND_LLM_MODE"
PACKAGE_REPO_ROOT = Path(__file__).resolve().parents[3]

Mode = Literal["live", "record", "replay"]
ThinkingSetting = Literal["on", "off"]
MODES: tuple[Mode, ...] = ("live", "record", "replay")


class FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ModelSpec(FrozenModel):
    alias: str
    id: str
    max_concurrency: int = Field(gt=0)


class RoleSettings(FrozenModel):
    model: str
    thinking: ThinkingSetting
    max_tokens: int = Field(gt=0)
    reasoning_budget: int | None = Field(default=None, gt=0)
    temperature: float | None = None
    top_p: float | None = None


class Price(FrozenModel):
    input_per_m: float = Field(gt=0)
    output_per_m: float = Field(gt=0)


class LLMSettings(FrozenModel):
    base_url: str
    models: dict[str, ModelSpec]
    roles: dict[str, RoleSettings]
    pricing: dict[str, Price]
    repo_root: Path
    config_dir: Path

    def role(self, name: str) -> RoleSettings:
        if name not in self.roles:
            raise ConfigurationError(f"Role '{name}' is not defined in models.yaml")
        return self.roles[name]

    def model(self, alias: str) -> ModelSpec:
        if alias not in self.models:
            raise ConfigurationError(f"Model alias '{alias}' is not defined in models.yaml")
        return self.models[alias]

    def model_for_role(self, name: str) -> ModelSpec:
        return self.model(self.role(name).model)

    def price_for(self, model_id: str) -> Price:
        if model_id not in self.pricing:
            raise ConfigurationError(f"Model '{model_id}' has no entry in pricing.yaml")
        return self.pricing[model_id]


def find_repo_root(start: Path | None = None) -> Path:
    override = os.environ.get(CONFIG_DIR_ENV)
    if override:
        return Path(override).resolve().parent
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "config" / "models.yaml").is_file():
            return candidate
    return PACKAGE_REPO_ROOT


def read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ConfigurationError(f"Configuration file not found: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigurationError(f"Configuration file must contain a mapping: {path}")
    return cast(dict[str, Any], data)


def load_settings(config_dir: Path | None = None) -> LLMSettings:
    repo_root = find_repo_root()
    directory = config_dir or Path(os.environ.get(CONFIG_DIR_ENV, repo_root / "config"))
    models_file = read_yaml(directory / "models.yaml")
    pricing_file = read_yaml(directory / "pricing.yaml")
    models = {
        alias: ModelSpec(alias=alias, **spec) for alias, spec in models_file["models"].items()
    }
    roles = {name: RoleSettings(**spec) for name, spec in models_file["roles"].items()}
    pricing = {model_id: Price(**spec) for model_id, spec in pricing_file.items()}
    for name, role in roles.items():
        if role.model not in models:
            raise ConfigurationError(f"Role '{name}' uses unknown model alias '{role.model}'")
    for spec in models.values():
        if spec.id not in pricing:
            raise ConfigurationError(f"Model '{spec.id}' has no entry in pricing.yaml")
    return LLMSettings(
        base_url=models_file["base_url"],
        models=models,
        roles=roles,
        pricing=pricing,
        repo_root=repo_root,
        config_dir=directory,
    )


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def mode_from_env() -> Mode:
    value = os.environ.get(MODE_ENV, "live").strip().lower()
    if value not in MODES:
        raise ConfigurationError(f"{MODE_ENV} must be one of {', '.join(MODES)}, got '{value}'")
    return value
