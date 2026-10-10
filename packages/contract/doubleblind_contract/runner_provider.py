import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx

from doubleblind_contract.binding import REQUEST_TIMEOUT_S, PartyClient
from doubleblind_contract.capture_server import WebhookCapture
from doubleblind_contract.runner_common import (
    failed_start_results,
    managed_apps,
    prepare,
    resolve_env,
    run_scenarios,
    running_apps,
    select_scenarios,
)
from doubleblind_contract.runtime import RunReport, utc_timestamp
from doubleblind_contract.steps import RunEnvironment


def run_provider_tests(
    contract_path: Path,
    binding_path: Path,
    scenario_ids: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> RunReport:
    variables_env = resolve_env(env)
    contract, bindings, secrets = prepare(contract_path, {"provider": binding_path}, variables_env)
    binding = bindings["provider"]
    scenarios = select_scenarios(contract, "provider", scenario_ids)
    report = RunReport(contract.id, contract.version, "provider", utc_timestamp())
    with (
        tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as log_dir,
        httpx.Client(timeout=REQUEST_TIMEOUT_S) as http,
        WebhookCapture() as capture,
    ):
        environment = RunEnvironment(
            contract=contract,
            side="provider",
            http=http,
            secrets=secrets,
            env=variables_env,
            parties={"provider": PartyClient(binding, http)},
            capture=capture,
            provider_base_url=binding.base_url,
            urls={"capture": {"url": capture.url}},
        )
        apps = managed_apps([(binding, binding_path)], variables_env, Path(log_dir))
        with running_apps(apps, environment.base_variables()) as start_error:
            if start_error is not None:
                report.scenarios = failed_start_results(scenarios, start_error)
            else:
                report.scenarios = run_scenarios(environment, scenarios, apps, capture.reset)
    report.finished_at = utc_timestamp()
    return report
