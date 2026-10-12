from collections.abc import Iterator
from pathlib import Path

import pytest
from demo_workspace import REFERENCE_CONTRACT, Workspace, build_workspace, run_repository_tests

from doubleblind_contract.runner import run_consumer_tests, run_e2e_tests, run_provider_tests
from doubleblind_contract.runtime import RunReport

pytestmark = pytest.mark.golden

CONSUMER_SCENARIOS = [f"S{number}" for number in range(1, 10)]
PROVIDER_SCENARIOS = ["P1", "P2", "P3", "P4"]
MAPPING = "spokes/app/integrations/parcelly/mapping.py"
WEBHOOK = "spokes/app/integrations/parcelly/webhook.py"
CLIENT = "spokes/app/integrations/parcelly/client.py"

MUTATIONS = {
    "kg_to_g_drops_the_fraction": (
        MAPPING,
        "return max(1, math.ceil(round(weight_kg * 1000, 6)))",
        "return max(1, int(weight_kg) * 1000)",
        {"S1", "S5"},
    ),
    "pallet_threshold_too_high": (
        MAPPING,
        "if weight_g > PALLET_THRESHOLD_G:",
        "if weight_g > PALLET_THRESHOLD_G * 2:",
        {"S2"},
    ),
    "webhook_signature_not_checked": (
        WEBHOOK,
        "return hmac.compare_digest(header_value, expected)",
        "return True",
        {"S8"},
    ),
    "national_phone_without_country_code": (
        MAPPING,
        'return f"+44{digits[1:]}"',
        'return f"+{digits[1:]}"',
        {"S1", "S3", "S5"},
    ),
    "base_url_path_dropped": (
        CLIENT,
        "f\"{os.environ['PARCELLY_BASE_URL'].rstrip('/')}{path}\"",
        "__import__(\"urllib.parse\").parse.urljoin(os.environ['PARCELLY_BASE_URL'], path)",
        {"S1", "S2", "S3", "S4", "S5"},
    ),
}


def failed(report: RunReport) -> set[str]:
    return {scenario.id for scenario in report.scenarios if scenario.status != "passed"}


def scenario_ids(report: RunReport) -> list[str]:
    return [scenario.id for scenario in report.scenarios]


def explain(report: RunReport) -> str:
    lines: list[str] = []
    for scenario in report.scenarios:
        if scenario.status != "passed":
            lines.append(f"{scenario.id} {scenario.status}")
            lines += [f"  {failure.render()}" for failure in scenario.failures]
            lines.append(scenario.app_log_tail[-2000:])
    return "\n".join(lines)


@pytest.fixture(scope="module")
def reference(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Workspace]:
    workspace = build_workspace(tmp_path_factory.mktemp("golden") / "reference")
    yield workspace
    workspace.remove()


@pytest.fixture(scope="module")
def untouched(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Workspace]:
    workspace = build_workspace(tmp_path_factory.mktemp("golden") / "untouched", False)
    yield workspace
    workspace.remove()


def test_reference_implementations_keep_the_repository_tests_green(reference: Workspace) -> None:
    for result in run_repository_tests(reference):
        assert result.returncode == 0, result.stdout + result.stderr


def test_reference_consumer_passes_every_consumer_scenario(reference: Workspace) -> None:
    report = run_consumer_tests(REFERENCE_CONTRACT, reference.consumer_binding, env=reference.env())

    assert scenario_ids(report) == CONSUMER_SCENARIOS
    assert report.ok, explain(report)


def test_reference_provider_passes_every_provider_scenario(reference: Workspace) -> None:
    report = run_provider_tests(REFERENCE_CONTRACT, reference.provider_binding, env=reference.env())

    assert scenario_ids(report) == PROVIDER_SCENARIOS
    assert report.ok, explain(report)


def test_both_reference_apps_pass_the_end_to_end_scenarios(reference: Workspace) -> None:
    report = run_e2e_tests(
        REFERENCE_CONTRACT,
        reference.consumer_binding,
        reference.provider_binding,
        env=reference.env(),
        start_apps=True,
    )

    assert scenario_ids(report) == ["E1", "E2"]
    assert report.ok, explain(report)


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_each_mutation_turns_exactly_the_expected_scenarios_red(
    reference: Workspace, name: str
) -> None:
    relative_path, old, new, expected = MUTATIONS[name]

    with reference.mutated(relative_path, old, new):
        report = run_consumer_tests(
            REFERENCE_CONTRACT, reference.consumer_binding, env=reference.env()
        )

    assert failed(report) == expected, explain(report)
    assert all(
        scenario.status == "failed" for scenario in report.scenarios if scenario.id in expected
    )


def test_kg_mutation_reports_the_exact_field(reference: Workspace) -> None:
    relative_path, old, new, _ = MUTATIONS["kg_to_g_drops_the_fraction"]

    with reference.mutated(relative_path, old, new):
        report = run_consumer_tests(
            REFERENCE_CONTRACT, reference.consumer_binding, scenario_ids=["S1"], env=reference.env()
        )

    failure = report.scenarios[0].failures[0]
    assert failure.path == "body.parcel.weight_g"
    assert (failure.expected, failure.actual) == (14500, 14000)


def test_provider_without_the_flag_fails_only_the_idempotency_scenario(
    reference: Workspace,
) -> None:
    flag_check = "if (!idempotencyKey && partner.flags.require_idempotency_key) {"

    with reference.mutated("parcelly/src/shipments.js", flag_check, "if (false) {"):
        report = run_provider_tests(
            REFERENCE_CONTRACT, reference.provider_binding, env=reference.env()
        )

    assert failed(report) == {"P1"}, explain(report)
    assert report.scenarios[0].failures[0].path == "status"


def test_untouched_demo_fails_with_readable_errors_not_crashes(untouched: Workspace) -> None:
    report = run_consumer_tests(REFERENCE_CONTRACT, untouched.consumer_binding, env=untouched.env())

    assert {scenario.status for scenario in report.scenarios} == {"failed"}
    first = report.scenarios[0].failures[0]
    assert first.path == "status"
    assert "consumer action 'reset' failed" in first.message
    assert report.scenarios[0].app_log_tail


def test_untouched_provider_fails_only_where_the_contract_needs_new_code(
    untouched: Workspace,
) -> None:
    report = run_provider_tests(REFERENCE_CONTRACT, untouched.provider_binding, env=untouched.env())

    assert failed(report) == {"P1"}, explain(report)


def test_missing_secret_is_a_configuration_error(reference: Workspace, tmp_path: Path) -> None:
    env = reference.env()
    del env["DOUBLEBLIND_SECRET_WEBHOOK_SECRET"]

    with pytest.raises(Exception, match="DOUBLEBLIND_SECRET_WEBHOOK_SECRET"):
        run_consumer_tests(REFERENCE_CONTRACT, reference.consumer_binding, env=env)


def test_cli_runs_provider_tests_and_writes_reports(reference: Workspace, tmp_path: Path) -> None:
    from typer.testing import CliRunner

    from doubleblind_contract.cli import app

    report_path, junit_path = tmp_path / "report.json", tmp_path / "junit.xml"

    result = CliRunner().invoke(
        app,
        [
            "test",
            "provider",
            str(REFERENCE_CONTRACT),
            "--binding",
            str(reference.provider_binding),
            "--scenarios",
            "P1,P3",
            "--report",
            str(report_path),
            "--junit",
            str(junit_path),
        ],
        env=reference.env(),
    )

    assert result.exit_code == 0, result.output
    assert "provider: 2 passed, 0 failed, 0 error" in result.output
    assert report_path.exists()
    assert junit_path.exists()
