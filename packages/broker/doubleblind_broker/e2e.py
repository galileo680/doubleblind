import os
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from doubleblind_contract.loader import load_binding_text
from doubleblind_contract.runner_e2e import run_e2e_tests
from doubleblind_contract.runtime import (
    Failure,
    RunReport,
    ScenarioResult,
    free_port,
    secret_env_name,
    utc_timestamp,
)

E2E_BINDING_FILE = "doubleblind.binding.e2e.yaml"


@dataclass(frozen=True)
class E2ERequest:
    session_id: str
    contract_yaml: str
    contract_id: str
    contract_version: int
    consumer_party: str
    provider_party: str
    consumer_binding: str
    provider_binding: str
    secrets: Mapping[str, str] = field(default_factory=dict)


class E2EExecutor(Protocol):
    def run(self, request: E2ERequest) -> RunReport: ...


def error_report(request: E2ERequest, message: str) -> RunReport:
    report = RunReport(request.contract_id, request.contract_version, "e2e", utc_timestamp())
    report.scenarios = [
        ScenarioResult(
            "E2E", "end-to-end run", "error", 0, [Failure(None, "", None, None, message)]
        )
    ]
    report.finished_at = utc_timestamp()
    return report


class UnavailableE2E:
    def run(self, request: E2ERequest) -> RunReport:
        return error_report(request, "no end-to-end executor is configured for this broker")


class LocalProcessE2E:
    def __init__(self, workspaces: Mapping[str, Path]) -> None:
        self.workspaces = {party: Path(path) for party, path in workspaces.items()}

    def environment(self, request: E2ERequest) -> dict[str, str]:
        consumer_port, provider_port = free_port(), free_port()
        env = {
            **os.environ,
            "CONSUMER_PORT": str(consumer_port),
            "CONSUMER_BASE_URL": f"http://127.0.0.1:{consumer_port}",
            "PROVIDER_PORT": str(provider_port),
            "PROVIDER_BASE_URL": f"http://127.0.0.1:{provider_port}",
        }
        env.update({secret_env_name(name): value for name, value in request.secrets.items()})
        consumer = load_binding_text(request.consumer_binding, env=env)
        if consumer.webhook_receiver_url:
            env["CONSUMER_WEBHOOK_URL"] = consumer.webhook_receiver_url
        return env

    def run(self, request: E2ERequest) -> RunReport:
        missing = [
            party
            for party in (request.consumer_party, request.provider_party)
            if party not in self.workspaces
        ]
        if missing:
            return error_report(request, f"no local workspace for {', '.join(missing)}")
        consumer_binding = self.workspaces[request.consumer_party] / E2E_BINDING_FILE
        provider_binding = self.workspaces[request.provider_party] / E2E_BINDING_FILE
        consumer_binding.write_text(request.consumer_binding, encoding="utf-8")
        provider_binding.write_text(request.provider_binding, encoding="utf-8")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            contract_path = Path(directory) / "contract.yaml"
            contract_path.write_text(request.contract_yaml, encoding="utf-8")
            return run_e2e_tests(
                contract_path,
                consumer_binding,
                provider_binding,
                env=self.environment(request),
                start_apps=True,
            )
