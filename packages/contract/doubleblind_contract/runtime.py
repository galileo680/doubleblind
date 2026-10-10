import os
import socket
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from doubleblind_contract.models import Contract, Side

ScenarioStatus = Literal["passed", "failed", "error"]
SECRET_ENV_PREFIX = "DOUBLEBLIND_SECRET_"


class ConfigurationError(Exception):
    def __init__(self, messages: Sequence[str]) -> None:
        self.messages = list(messages)
        super().__init__("\n".join(self.messages))


@dataclass(frozen=True)
class Failure:
    step: int | None
    path: str
    expected: Any
    actual: Any
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "path": self.path,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
        }

    def render(self) -> str:
        where = "after all steps" if self.step is None else f"step {self.step}"
        location = f" {self.path}:" if self.path else ""
        details = ""
        if self.expected is not None or self.actual is not None:
            details = f" (expected {self.expected!r}, got {self.actual!r})"
        return f"{where}:{location} {self.message}{details}"


@dataclass
class ScenarioResult:
    id: str
    title: str
    status: ScenarioStatus
    duration_ms: int
    failures: list[Failure] = field(default_factory=list)
    app_log_tail: str = ""

    def to_dict(self, include_logs: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "duration_ms": self.duration_ms,
            "failures": [failure.to_dict() for failure in self.failures],
        }
        if include_logs:
            data["app_log_tail"] = self.app_log_tail
        return data


@dataclass
class RunReport:
    contract_id: str
    contract_version: int
    side: Side
    started_at: str
    finished_at: str = ""
    scenarios: list[ScenarioResult] = field(default_factory=list)

    @property
    def summary(self) -> dict[str, int]:
        return {
            status: sum(1 for scenario in self.scenarios if scenario.status == status)
            for status in ("passed", "failed", "error")
        }

    @property
    def ok(self) -> bool:
        return all(scenario.status == "passed" for scenario in self.scenarios)

    def statuses(self) -> dict[str, ScenarioStatus]:
        return {scenario.id: scenario.status for scenario in self.scenarios}

    def to_dict(self, include_logs: bool = True) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "contract_version": self.contract_version,
            "side": self.side,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "summary": self.summary,
            "scenarios": [scenario.to_dict(include_logs) for scenario in self.scenarios],
        }


def utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port: int = sock.getsockname()[1]
        return port


def secret_env_name(name: str) -> str:
    return f"{SECRET_ENV_PREFIX}{name.upper()}"


def load_secrets(contract: Contract, env: Mapping[str, str]) -> dict[str, str]:
    missing = [
        secret_env_name(secret.name)
        for secret in contract.secrets
        if secret_env_name(secret.name) not in env
    ]
    if missing:
        raise ConfigurationError([f"secret value missing: set {name}" for name in missing])
    return {secret.name: env[secret_env_name(secret.name)] for secret in contract.secrets}


def read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def runner_env(env_files: Sequence[Path] = ()) -> dict[str, str]:
    env = dict(os.environ)
    for path in env_files:
        env.update(read_env_file(path))
    return env
