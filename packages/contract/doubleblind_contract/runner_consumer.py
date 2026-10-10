import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

import httpx

from doubleblind_contract.binding import REQUEST_TIMEOUT_S, PartyClient
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
from doubleblind_contract.stub_server import ProviderStub


def run_consumer_tests(
    contract_path: Path,
    binding_path: Path,
    scenario_ids: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> RunReport:
    variables_env = resolve_env(env)
    contract, bindings, secrets = prepare(contract_path, {"consumer": binding_path}, variables_env)
    binding = bindings["consumer"]
    scenarios = select_scenarios(contract, "consumer", scenario_ids)
    report = RunReport(contract.id, contract.version, "consumer", utc_timestamp())
    with (
        tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as log_dir,
        httpx.Client(timeout=REQUEST_TIMEOUT_S) as http,
        ProviderStub(contract, secrets, binding.webhook_receiver_url) as stub,
    ):
        environment = RunEnvironment(
            contract=contract,
            side="consumer",
            http=http,
            secrets=secrets,
            env=variables_env,
            parties={"consumer": PartyClient(binding, http)},
            stub=stub,
            urls={"stub": {"url": stub.url}},
        )
        apps = managed_apps([(binding, binding_path)], variables_env, Path(log_dir))
        with running_apps(apps, environment.base_variables()) as start_error:
            if start_error is not None:
                report.scenarios = failed_start_results(scenarios, start_error)
            else:
                report.scenarios = run_scenarios(environment, scenarios, apps, stub.reset)
    report.finished_at = utc_timestamp()
    return report
