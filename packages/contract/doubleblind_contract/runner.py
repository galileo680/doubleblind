import tempfile
import time
from collections.abc import Iterator, Mapping, Sequence
from contextlib import ExitStack, contextmanager
from pathlib import Path
from typing import Any

import httpx

from doubleblind_contract.binding import REQUEST_TIMEOUT_S, AppStartError, ManagedApp, PartyClient
from doubleblind_contract.capture_server import WebhookCapture
from doubleblind_contract.loader import ContractLoadError, load_binding, load_contract
from doubleblind_contract.models import Binding, Contract, PartyRole, Scenario, Side
from doubleblind_contract.runtime import (
    ConfigurationError,
    Failure,
    RunReport,
    ScenarioResult,
    load_secrets,
    utc_timestamp,
)
from doubleblind_contract.steps import RunEnvironment, ScenarioExecutor
from doubleblind_contract.stub_server import ProviderStub
from doubleblind_contract.validate import validate_contract_file

BindingPaths = Mapping[PartyRole, Path]
APP_START_ORDER: tuple[PartyRole, ...] = ("provider", "consumer")
LOG_QUIET_S = 0.3
LOG_SETTLE_MAX_S = 3.0


def load_bindings(paths: BindingPaths, env: Mapping[str, str]) -> dict[PartyRole, Binding]:
    bindings: dict[PartyRole, Binding] = {}
    problems: list[str] = []
    for role, path in paths.items():
        try:
            binding = load_binding(path, env=env)
        except ContractLoadError as error:
            problems += [f"{path.name}: {problem.render()}" for problem in error.problems]
            continue
        except OSError as error:
            problems.append(f"{path}: cannot read file: {error.strerror or error}")
            continue
        if binding.side != role:
            problems.append(f"{path.name}: expected a {role} binding, got side '{binding.side}'")
            continue
        bindings[role] = binding
    if problems:
        raise ConfigurationError(problems)
    return bindings


def prepare(
    contract_path: Path, binding_paths: BindingPaths, env: Mapping[str, str]
) -> tuple[Contract, dict[PartyRole, Binding], dict[str, str]]:
    bindings = load_bindings(binding_paths, env)
    try:
        report = validate_contract_file(contract_path, list(bindings.values()))
    except OSError as error:
        raise ConfigurationError(
            [f"{contract_path}: cannot read file: {error.strerror or error}"]
        ) from error
    if not report.ok:
        raise ConfigurationError([finding.render() for finding in report.errors])
    contract = load_contract(contract_path)
    return contract, bindings, load_secrets(contract, env)


def select_scenarios(
    contract: Contract, side: Side, scenario_ids: Sequence[str] | None
) -> list[Scenario]:
    available = contract.scenarios_for(side)
    if not scenario_ids:
        return available
    known = {scenario.id for scenario in available}
    unknown = [scenario_id for scenario_id in scenario_ids if scenario_id not in known]
    if unknown:
        raise ConfigurationError(
            [f"no {side} scenario with id {scenario_id}" for scenario_id in unknown]
        )
    return [scenario for scenario in available if scenario.id in scenario_ids]


def managed_apps(
    parties: Sequence[tuple[Binding, Path]], env: Mapping[str, str], log_dir: Path
) -> list[ManagedApp]:
    return [
        ManagedApp(binding.side, binding.app, binding.base_url, path.parent, env, log_dir)
        for binding, path in parties
        if binding.app is not None and binding.app.manage
    ]


@contextmanager
def running_apps(
    apps: Sequence[ManagedApp], variables: Mapping[str, Any]
) -> Iterator[AppStartError | None]:
    started: list[ManagedApp] = []
    error: AppStartError | None = None
    try:
        for app in apps:
            started.append(app)
            app.start(variables)
    except AppStartError as start_error:
        error = start_error
    try:
        yield error
    finally:
        for app in reversed(started):
            app.stop()


def apps_log_tail(apps: Sequence[ManagedApp]) -> str:
    return "\n".join(f"--- {app.name} app ---\n{app.log_tail()}" for app in apps)


def settled_log_tail(apps: Sequence[ManagedApp]) -> str:
    deadline = time.monotonic() + LOG_SETTLE_MAX_S
    tail = apps_log_tail(apps)
    while time.monotonic() < deadline:
        time.sleep(LOG_QUIET_S)
        latest = apps_log_tail(apps)
        if latest == tail:
            break
        tail = latest
    return tail


def failed_start_results(
    scenarios: Sequence[Scenario], error: AppStartError
) -> list[ScenarioResult]:
    return [
        ScenarioResult(
            scenario.id,
            scenario.title,
            "error",
            0,
            [Failure(None, "app", None, None, f"the app did not start: {error}")],
            error.log_tail,
        )
        for scenario in scenarios
    ]


def run_scenarios(
    environment: RunEnvironment, scenarios: Sequence[Scenario], apps: Sequence[ManagedApp]
) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    for scenario in scenarios:
        if environment.stub is not None:
            environment.stub.reset()
        if environment.capture is not None:
            environment.capture.reset()
        result = ScenarioExecutor(environment, scenario).run()
        if result.status != "passed" and apps:
            result.app_log_tail = settled_log_tail(apps)
        results.append(result)
    return results


def run_tests(
    side: Side,
    contract_path: Path,
    binding_paths: BindingPaths,
    scenario_ids: Sequence[str] | None,
    env: Mapping[str, str],
    start_apps: bool = True,
) -> RunReport:
    contract, bindings, secrets = prepare(contract_path, binding_paths, env)
    scenarios = select_scenarios(contract, side, scenario_ids)
    report = RunReport(contract.id, contract.version, side, utc_timestamp())
    with (
        tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as log_dir,
        httpx.Client(timeout=REQUEST_TIMEOUT_S) as http,
        ExitStack() as servers,
    ):
        environment = RunEnvironment(
            contract=contract,
            side=side,
            http=http,
            secrets=secrets,
            env=env,
            parties={role: PartyClient(binding, http) for role, binding in bindings.items()},
        )
        if side == "consumer":
            stub = servers.enter_context(
                ProviderStub(contract, secrets, bindings["consumer"].webhook_receiver_url)
            )
            environment.stub = stub
            environment.urls = {"stub": {"url": stub.url}}
        elif side == "provider":
            capture = servers.enter_context(WebhookCapture())
            environment.capture = capture
            environment.provider_base_url = bindings["provider"].base_url
            environment.urls = {"capture": {"url": capture.url}}
        else:
            environment.provider_base_url = bindings["provider"].base_url
            environment.urls = {
                "stub": {"url": bindings["provider"].base_url},
                "capture": {"url": bindings["consumer"].webhook_receiver_url or ""},
            }
        parties = [
            (bindings[role], binding_paths[role]) for role in APP_START_ORDER if role in bindings
        ]
        apps = managed_apps(parties, env, Path(log_dir)) if start_apps else []
        with running_apps(apps, environment.base_variables()) as start_error:
            if start_error is not None:
                report.scenarios = failed_start_results(scenarios, start_error)
            else:
                report.scenarios = run_scenarios(environment, scenarios, apps)
    report.finished_at = utc_timestamp()
    return report


def run_consumer_tests(
    contract_path: Path,
    binding_path: Path,
    scenario_ids: Sequence[str] | None = None,
    *,
    env: Mapping[str, str],
) -> RunReport:
    return run_tests("consumer", contract_path, {"consumer": binding_path}, scenario_ids, env)


def run_provider_tests(
    contract_path: Path,
    binding_path: Path,
    scenario_ids: Sequence[str] | None = None,
    *,
    env: Mapping[str, str],
) -> RunReport:
    return run_tests("provider", contract_path, {"provider": binding_path}, scenario_ids, env)


def run_e2e_tests(
    contract_path: Path,
    consumer_binding_path: Path,
    provider_binding_path: Path,
    scenario_ids: Sequence[str] | None = None,
    *,
    env: Mapping[str, str],
    start_apps: bool = False,
) -> RunReport:
    binding_paths: BindingPaths = {
        "consumer": consumer_binding_path,
        "provider": provider_binding_path,
    }
    return run_tests("e2e", contract_path, binding_paths, scenario_ids, env, start_apps)
