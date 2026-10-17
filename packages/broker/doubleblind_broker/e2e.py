import asyncio
import os
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import httpx

from doubleblind_contract.loader import load_binding_text
from doubleblind_contract.models import Binding
from doubleblind_contract.runner import run_e2e_tests
from doubleblind_contract.runtime import (
    Failure,
    RunReport,
    ScenarioResult,
    free_port,
    secret_env_name,
    utc_timestamp,
)
from doubleblind_contract.templating import render
from doubleblind_sandbox.provider import ServiceHandle, ServiceSandbox, start_relayed_app

E2E_BINDING_FILE = "doubleblind.binding.e2e.yaml"
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
    secrets: Mapping[str, str]
    images: Mapping[str, str]
    relay_tokens: Mapping[str, str]


E2EOutcome = tuple[RunReport, float]


class E2EExecutor(Protocol):
    def run(self, request: E2ERequest) -> E2EOutcome: ...


def error_report(request: E2ERequest, message: str) -> RunReport:
    report = RunReport(request.contract_id, request.contract_version, "e2e", utc_timestamp())
    report.scenarios = [
        ScenarioResult(
            "E2E", "end-to-end run", "error", 0, [Failure(None, "", None, None, message)]
        )
    ]
    report.finished_at = utc_timestamp()
    return report


def e2e_environment(
    request: E2ERequest, inherited: Mapping[str, str], base_url: Callable[[str, int], str]
) -> dict[str, str]:
    env = dict(inherited)
    for prefix, party in (
        ("CONSUMER", request.consumer_party),
        ("PROVIDER", request.provider_party),
    ):
        port = free_port()
        env[f"{prefix}_PORT"] = str(port)
        env[f"{prefix}_BASE_URL"] = base_url(party, port)
    env.update({secret_env_name(name): value for name, value in request.secrets.items()})
    consumer = load_binding_text(request.consumer_binding, env=env)
    if consumer.webhook_receiver_url:
        env["CONSUMER_WEBHOOK_URL"] = consumer.webhook_receiver_url
    return env


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
    def run(self, request: E2ERequest) -> E2EOutcome:
        return error_report(request, "no end-to-end executor is configured for this broker"), 0.0


class LocalProcessE2E:
    def __init__(self, workspaces: Mapping[str, Path]) -> None:
        self.workspaces = {party: Path(path) for party, path in workspaces.items()}

    def run(self, request: E2ERequest) -> E2EOutcome:
        missing = [
            party
            for party in (request.consumer_party, request.provider_party)
            if party not in self.workspaces
        ]
        if missing:
            return error_report(request, f"no local workspace for {', '.join(missing)}"), 0.0
        consumer_binding = self.workspaces[request.consumer_party] / E2E_BINDING_FILE
        provider_binding = self.workspaces[request.provider_party] / E2E_BINDING_FILE
        consumer_binding.write_text(request.consumer_binding, encoding="utf-8")
        provider_binding.write_text(request.provider_binding, encoding="utf-8")
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            contract_path = Path(directory) / "contract.yaml"
            contract_path.write_text(request.contract_yaml, encoding="utf-8")
            report = run_e2e_tests(
                contract_path,
                consumer_binding,
                provider_binding,
                env=e2e_environment(
                    request, os.environ, lambda party, port: f"http://127.0.0.1:{port}"
                ),
                start_apps=True,
            )
            return report, 0.0


class E2ESetupError(Exception):
    pass


@dataclass(frozen=True)
class E2EParty:
    key: str
    binding: Binding
    port: int
    url: str


def gateway_environment(request: E2ERequest, broker_url: str) -> dict[str, str]:
    gateway = f"{broker_url}/gw/{request.session_id}"
    return e2e_environment(request, {}, lambda party, port: f"{gateway}/{party}")


def gateway_parties(request: E2ERequest, env: Mapping[str, str]) -> list[E2EParty]:
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


def app_env(request: E2ERequest, env: Mapping[str, str], party: E2EParty) -> dict[str, str]:
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


async def wait_until_healthy(parties: Sequence[E2EParty]) -> None:
    deadline = time.monotonic() + HEALTH_TIMEOUT_S
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


class SandboxGatewayE2E:
    def __init__(self, sandbox_factory: Callable[[], ServiceSandbox], broker_url: str) -> None:
        self.sandbox_factory = sandbox_factory
        self.broker_url = broker_url.rstrip("/")

    def run(self, request: E2ERequest) -> E2EOutcome:
        parties = (request.consumer_party, request.provider_party)
        missing = [party for party in parties if party not in request.images]
        if missing:
            message = f"no green workspace image for {', '.join(missing)}"
            return error_report(request, message), 0.0
        return asyncio.run(self.execute(request))

    async def execute(self, request: E2ERequest) -> E2EOutcome:
        env = gateway_environment(request, self.broker_url)
        parties = gateway_parties(request, env)
        sandbox = self.sandbox_factory()
        handles: list[ServiceHandle] = []
        try:
            for party in parties:
                workspace = await sandbox.open_workspace(party.key, request.images[party.key])
                handles.append(
                    await start_relayed_app(
                        sandbox,
                        workspace,
                        self.broker_url,
                        request.session_id,
                        party.key,
                        party.port,
                        app_env(request, env, party),
                    )
                )
            await wait_until_healthy(parties)
            report = await asyncio.to_thread(run_with_files, request, {**os.environ, **env})
        except E2ESetupError as error:
            report = error_report(request, str(error))
        finally:
            for handle in handles:
                await sandbox.stop_service(handle)
            await sandbox.aclose()
        return report, sandbox.spent_usd


class AgentServedE2E:
    def __init__(self, broker_url: str | None) -> None:
        self.broker_url = broker_url.rstrip("/") if broker_url else None
        self.serving: dict[str, dict[str, dict[str, Any]]] = {}

    async def run(
        self, request: E2ERequest, attempt: int, announce: Callable[[], None]
    ) -> E2EOutcome:
        if self.broker_url is None:
            announce()
            message = "set BROKER_PUBLIC_URL so the agents' sandboxes can reach the gateway"
            return error_report(request, message), 0.0
        env = gateway_environment(request, self.broker_url)
        try:
            parties = gateway_parties(request, env)
            self.serving[request.session_id] = {
                party.key: {
                    "attempt": attempt,
                    "broker_url": self.broker_url,
                    "port": party.port,
                    "env": app_env(request, env, party),
                }
                for party in parties
            }
            announce()
            await wait_until_healthy(parties)
            report = await asyncio.to_thread(run_with_files, request, {**os.environ, **env})
        except E2ESetupError as error:
            report = error_report(request, str(error))
        finally:
            self.serving.pop(request.session_id, None)
        return report, 0.0
