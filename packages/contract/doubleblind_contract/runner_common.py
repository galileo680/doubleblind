import os
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from doubleblind_contract.binding import AppStartError, ManagedApp
from doubleblind_contract.loader import ContractLoadError, load_binding, load_contract
from doubleblind_contract.models import Binding, Contract, PartyRole, Scenario, Side
from doubleblind_contract.runtime import (
    ConfigurationError,
    Failure,
    ScenarioResult,
    load_secrets,
)
from doubleblind_contract.steps import RunEnvironment, ScenarioExecutor
from doubleblind_contract.validate import validate_contract_file

BindingPaths = Mapping[PartyRole, Path]


def resolve_env(env: Mapping[str, str] | None) -> dict[str, str]:
    return dict(os.environ if env is None else env)


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
    environment: RunEnvironment,
    scenarios: Sequence[Scenario],
    apps: Sequence[ManagedApp],
    before_each: Callable[[], None],
) -> list[ScenarioResult]:
    results: list[ScenarioResult] = []
    for scenario in scenarios:
        before_each()
        result = ScenarioExecutor(environment, scenario).run()
        if result.status != "passed" and apps:
            result.app_log_tail = apps_log_tail(apps)
        results.append(result)
    return results
