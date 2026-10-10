import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx

from doubleblind_contract.binding import REQUEST_TIMEOUT_S, ManagedApp, PartyClient
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


def run_e2e_tests(
    contract_path: Path,
    consumer_binding_path: Path,
    provider_binding_path: Path,
    scenario_ids: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
    start_apps: bool = False,
) -> RunReport:
    variables_env = resolve_env(env)
    contract, bindings, secrets = prepare(
        contract_path,
        {"consumer": consumer_binding_path, "provider": provider_binding_path},
        variables_env,
    )
    consumer, provider = bindings["consumer"], bindings["provider"]
    scenarios = select_scenarios(contract, "e2e", scenario_ids)
    report = RunReport(contract.id, contract.version, "e2e", utc_timestamp())
    with (
        tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as log_dir,
        httpx.Client(timeout=REQUEST_TIMEOUT_S) as http,
    ):
        environment = RunEnvironment(
            contract=contract,
            side="e2e",
            http=http,
            secrets=secrets,
            env=variables_env,
            parties={
                "consumer": PartyClient(consumer, http),
                "provider": PartyClient(provider, http),
            },
            provider_base_url=provider.base_url,
            urls={
                "stub": {"url": provider.base_url},
                "capture": {"url": consumer.webhook_receiver_url or ""},
            },
        )
        apps: list[ManagedApp] = []
        if start_apps:
            apps = managed_apps(
                [(provider, provider_binding_path), (consumer, consumer_binding_path)],
                variables_env,
                Path(log_dir),
            )
        with running_apps(apps, environment.base_variables()) as start_error:
            if start_error is not None:
                report.scenarios = failed_start_results(scenarios, start_error)
            else:
                report.scenarios = run_scenarios(environment, scenarios, apps, lambda: None)
    report.finished_at = utc_timestamp()
    return report
