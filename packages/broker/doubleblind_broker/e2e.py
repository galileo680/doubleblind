import asyncio
import os
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import httpx

import doubleblind_sandbox.relay_client as relay_module
from doubleblind_contract.loader import load_binding_text
from doubleblind_contract.models import Binding
from doubleblind_contract.runner_e2e import run_e2e_tests
from doubleblind_contract.runtime import (
    Failure,
    RunReport,
    ScenarioResult,
    free_port,
    secret_env_name,
    utc_timestamp,
)
from doubleblind_contract.templating import render
from doubleblind_sandbox.provider import ServiceHandle, ServiceSandbox, repo_path

E2E_BINDING_FILE = "doubleblind.binding.e2e.yaml"
RELAY_SOURCE = Path(relay_module.__file__).read_bytes()
RELAY_FILE = ".doubleblind-relay.py"
HEALTH_TIMEOUT_S = 240.0
HEALTH_RETRY_S = 1.0


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
    images: Mapping[str, str] = field(default_factory=dict)
    relay_tokens: Mapping[str, str] = field(default_factory=dict)


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


def run_with_files(request: E2ERequest, env: Mapping[str, str]) -> RunReport:
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
        root = Path(directory)
        contract_path = root / "contract.yaml"
        consumer_binding = root / "consumer.binding.yaml"
        provider_binding = root / "provider.binding.yaml"
        contract_path.write_text(request.contract_yaml, encoding="utf-8")
        consumer_binding.write_text(request.consumer_binding, encoding="utf-8")
        provider_binding.write_text(request.provider_binding, encoding="utf-8")
        return run_e2e_tests(
            contract_path, consumer_binding, provider_binding, env=env, start_apps=False
        )


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


class E2ESetupError(Exception):
    pass


@dataclass(frozen=True)
class E2EParty:
    key: str
    binding: Binding
    port: int
    url: str


class SandboxGatewayE2E:
    def __init__(
        self,
        sandbox_factory: Callable[[], ServiceSandbox],
        broker_url: str,
        health_timeout_s: float = HEALTH_TIMEOUT_S,
    ) -> None:
        self.sandbox_factory = sandbox_factory
        self.broker_url = broker_url.rstrip("/")
        self.health_timeout_s = health_timeout_s

    def run(self, request: E2ERequest) -> RunReport:
        parties = (request.consumer_party, request.provider_party)
        missing = [party for party in parties if party not in request.images]
        if missing:
            return error_report(request, f"no green workspace image for {', '.join(missing)}")
        absent = [party for party in parties if party not in request.relay_tokens]
        if absent:
            return error_report(request, f"no relay token for {', '.join(absent)}")
        return asyncio.run(self.execute(request))

    def environment(self, request: E2ERequest) -> dict[str, str]:
        gateway = f"{self.broker_url}/gw/{request.session_id}"
        consumer_port, provider_port = free_port(), free_port()
        env = {
            "CONSUMER_PORT": str(consumer_port),
            "CONSUMER_BASE_URL": f"{gateway}/{request.consumer_party}",
            "PROVIDER_PORT": str(provider_port),
            "PROVIDER_BASE_URL": f"{gateway}/{request.provider_party}",
        }
        env.update({secret_env_name(name): value for name, value in request.secrets.items()})
        consumer = load_binding_text(request.consumer_binding, env=env)
        if consumer.webhook_receiver_url:
            env["CONSUMER_WEBHOOK_URL"] = consumer.webhook_receiver_url
        return env

    def parties(self, request: E2ERequest, env: Mapping[str, str]) -> list[E2EParty]:
        return [
            E2EParty(
                request.provider_party,
                load_binding_text(request.provider_binding, env=env),
                int(env["PROVIDER_PORT"]),
                env["PROVIDER_BASE_URL"],
            ),
            E2EParty(
                request.consumer_party,
                load_binding_text(request.consumer_binding, env=env),
                int(env["CONSUMER_PORT"]),
                env["CONSUMER_BASE_URL"],
            ),
        ]

    def app_env(
        self, request: E2ERequest, env: Mapping[str, str], party: E2EParty
    ) -> dict[str, str]:
        app = party.binding.app
        if app is None or app.start is None:
            raise E2ESetupError(f"{party.key}: the binding has no app start command")
        variables = {
            "secrets": dict(request.secrets),
            "env": dict(env),
            "stub": {"url": env["PROVIDER_BASE_URL"]},
            "capture": {"url": env.get("CONSUMER_WEBHOOK_URL", "")},
        }
        rendered = {key: str(value) for key, value in render(app.env, variables).items()}
        return {
            **rendered,
            "DOUBLEBLIND_RELAY_TOKEN": request.relay_tokens[party.key],
            "DOUBLEBLIND_APP_COMMAND": app.start,
            "DOUBLEBLIND_APP_CWD": app.cwd,
            "PYTHONUNBUFFERED": "1",
        }

    def relay_command(self, sandbox: ServiceSandbox, request: E2ERequest, party: E2EParty) -> str:
        return (
            f"{sandbox.python} {RELAY_FILE} --broker {self.broker_url}"
            f" --session {request.session_id} --party {party.key}"
            f" --target http://127.0.0.1:{party.port}"
        )

    async def wait_until_healthy(self, parties: Sequence[E2EParty]) -> None:
        deadline = time.monotonic() + self.health_timeout_s
        async with httpx.AsyncClient(timeout=30.0) as http:
            for party in parties:
                health = party.binding.app.healthcheck if party.binding.app else None
                if health is None:
                    continue
                while True:
                    try:
                        response = await http.request(health.method, f"{party.url}{health.path}")
                        if response.status_code < 400:
                            break
                    except httpx.TransportError:
                        pass
                    if time.monotonic() > deadline:
                        raise E2ESetupError(f"{party.key}: the app did not become healthy")
                    await asyncio.sleep(HEALTH_RETRY_S)

    async def execute(self, request: E2ERequest) -> RunReport:
        env = self.environment(request)
        parties = self.parties(request, env)
        sandbox = self.sandbox_factory()
        handles: list[ServiceHandle] = []
        try:
            for party in parties:
                workspace = await sandbox.open_workspace(party.key, request.images[party.key])
                handles.append(
                    await sandbox.start_service(
                        workspace,
                        self.relay_command(sandbox, request, party),
                        env=self.app_env(request, env, party),
                        files={repo_path(RELAY_FILE): RELAY_SOURCE},
                    )
                )
            await self.wait_until_healthy(parties)
            return await asyncio.to_thread(run_with_files, request, {**os.environ, **env})
        except E2ESetupError as error:
            return error_report(request, str(error))
        finally:
            for handle in handles:
                await sandbox.stop_service(handle)
            await sandbox.aclose()
