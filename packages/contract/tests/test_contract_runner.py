import json
import os
import sys
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import pytest
from contract_fixtures import FIXTURES_DIR, MINIMAL, PARCELLY_BINDING, SIGNED, SPOKES_BINDING
from typer.testing import CliRunner

from doubleblind_contract.binding import AppStartError, ManagedApp, resolve_command
from doubleblind_contract.cli import app
from doubleblind_contract.models import AppSpec
from doubleblind_contract.report import render_console, write_json, write_junit
from doubleblind_contract.runner_consumer import run_consumer_tests
from doubleblind_contract.runner_e2e import run_e2e_tests
from doubleblind_contract.runner_provider import run_provider_tests
from doubleblind_contract.runtime import (
    ConfigurationError,
    Failure,
    RunReport,
    ScenarioResult,
    free_port,
    read_env_file,
    runner_env,
)

ENV = {
    "CONSUMER_BASE_URL": "http://127.0.0.1:1",
    "PROVIDER_BASE_URL": "http://127.0.0.1:2",
    "DOUBLEBLIND_SECRET_PARTNER_API_KEY": "k",
    "DOUBLEBLIND_SECRET_WEBHOOK_SECRET": "s",
}

runner = CliRunner()


def sample_report() -> RunReport:
    report = RunReport("spokes-parcelly-shipping", 9, "consumer", "2026-10-09T10:00:00Z")
    report.scenarios = [
        ScenarioResult("S1", "Passing", "passed", 12),
        ScenarioResult(
            "S4",
            "Failing",
            "failed",
            412,
            [
                Failure(
                    3,
                    "body.receiver.address.street",
                    "al. Jana Pawła II",
                    "al. Jana",
                    "value mismatch",
                )
            ],
            "app log line",
        ),
        ScenarioResult("S5", "Broken", "error", 0, [Failure(None, "", None, None, "ConnectError")]),
    ]
    report.finished_at = "2026-10-09T10:00:05Z"
    return report


def test_report_has_the_documented_json_shape(tmp_path: Path) -> None:
    path = tmp_path / "out" / "report.json"

    write_json(sample_report(), path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["summary"] == {"passed": 1, "failed": 1, "error": 1}
    assert data["scenarios"][1]["failures"][0] == {
        "step": 3,
        "path": "body.receiver.address.street",
        "expected": "al. Jana Pawła II",
        "actual": "al. Jana",
        "message": "value mismatch",
    }
    assert data["scenarios"][1]["app_log_tail"] == "app log line"
    assert "app_log_tail" not in sample_report().to_dict(include_logs=False)["scenarios"][0]


def test_report_can_be_written_as_junit(tmp_path: Path) -> None:
    path = tmp_path / "junit.xml"

    write_junit(sample_report(), path)

    suite = ElementTree.parse(path).getroot()
    assert suite.attrib["tests"] == "3"
    assert suite.attrib["failures"] == "1"
    assert suite.attrib["errors"] == "1"
    cases = suite.findall("testcase")
    assert cases[1].find("failure") is not None
    assert cases[2].find("error") is not None
    assert cases[1].find("system-out").text == "app log line"


def test_console_lines_explain_failures() -> None:
    lines = render_console(sample_report())

    assert lines[0] == "PASS  S1 Passing (12 ms)"
    assert lines[2] == (
        "      step 3: body.receiver.address.street: value mismatch "
        "(expected 'al. Jana Pawła II', got 'al. Jana')"
    )
    assert lines[-1] == "consumer: 1 passed, 1 failed, 1 error"
    assert "after all steps: ConnectError" in lines[4]


def test_invalid_contract_is_a_configuration_error() -> None:
    with pytest.raises(ConfigurationError, match="rule 2"):
        run_consumer_tests(
            FIXTURES_DIR / "broken_rule2_unknown_operation.yaml", SPOKES_BINDING, env=ENV
        )


def test_binding_of_the_wrong_side_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="expected a consumer binding"):
        run_consumer_tests(SIGNED, PARCELLY_BINDING, env=ENV)


def test_missing_binding_file_and_environment_are_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="cannot read file"):
        run_provider_tests(SIGNED, tmp_path / "missing.yaml", env=ENV)
    with pytest.raises(ConfigurationError, match="PROVIDER_BASE_URL is not set"):
        run_provider_tests(SIGNED, PARCELLY_BINDING, env={})


def test_missing_contract_file_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigurationError, match="cannot read file"):
        run_consumer_tests(tmp_path / "missing.yaml", SPOKES_BINDING, env=ENV)


def test_missing_secret_is_reported() -> None:
    env = {key: value for key, value in ENV.items() if "WEBHOOK" not in key}

    with pytest.raises(ConfigurationError, match="DOUBLEBLIND_SECRET_WEBHOOK_SECRET"):
        run_e2e_tests(SIGNED, SPOKES_BINDING, PARCELLY_BINDING, env=env)


def test_unknown_scenario_ids_are_rejected() -> None:
    with pytest.raises(ConfigurationError, match="no e2e scenario with id E9"):
        run_e2e_tests(SIGNED, SPOKES_BINDING, PARCELLY_BINDING, ["E1", "E9"], env=ENV)


def test_cli_reports_configuration_errors_with_exit_code_two() -> None:
    result = runner.invoke(
        app, ["test", "consumer", str(SIGNED), "--binding", str(PARCELLY_BINDING)]
    )

    assert result.exit_code == 2
    assert "configuration error" in result.output


def test_env_file_values_are_added(tmp_path: Path) -> None:
    env_file = tmp_path / "test.env"
    env_file.write_text(
        '# secrets for tests\nDOUBLEBLIND_SECRET_X="abc"\n\nPLAIN=value\nbroken line\n',
        encoding="utf-8",
    )

    assert read_env_file(env_file) == {"DOUBLEBLIND_SECRET_X": "abc", "PLAIN": "value"}
    assert runner_env([env_file])["PLAIN"] == "value"


def python_app(tmp_path: Path, start: str, healthcheck: bool = True) -> ManagedApp:
    port = free_port()
    spec = AppSpec.model_validate(
        {
            "manage": True,
            "start": start.replace("PORT", str(port)),
            **({"healthcheck": {"path": "/"}} if healthcheck else {}),
        }
    )
    return ManagedApp(
        "demo", spec, f"http://127.0.0.1:{port}", tmp_path, dict(os.environ), tmp_path
    )


def test_managed_app_starts_answers_and_stops(tmp_path: Path) -> None:
    managed = python_app(tmp_path, "python -m http.server PORT --bind 127.0.0.1")

    managed.start({})
    try:
        assert managed.process is not None and managed.process.poll() is None
    finally:
        managed.stop()

    assert managed.process.poll() is not None


def test_app_that_exits_reports_its_log(tmp_path: Path) -> None:
    managed = python_app(tmp_path, "python -c \"print('boom'); raise SystemExit(3)\"")

    with pytest.raises(AppStartError, match="exited with code 3") as caught:
        managed.start({})
    managed.stop()

    assert "boom" in caught.value.log_tail


def test_app_without_healthcheck_is_given_a_moment(tmp_path: Path) -> None:
    managed = python_app(tmp_path, "python -m http.server PORT", healthcheck=False)

    managed.start({})
    managed.stop()


def test_app_that_never_becomes_healthy_times_out(tmp_path: Path) -> None:
    managed = python_app(tmp_path, 'python -c "import time; time.sleep(30)"')

    with pytest.raises(AppStartError, match="no healthy answer"):
        managed.start({}, timeout_s=0.5)
    managed.stop()


def test_missing_start_command_is_an_error(tmp_path: Path) -> None:
    spec = AppSpec.model_validate({"manage": False})
    managed = ManagedApp("demo", spec, "http://127.0.0.1:1", tmp_path, {}, tmp_path)

    with pytest.raises(AppStartError, match="no start command"):
        managed.start({})
    with pytest.raises(AppStartError, match="empty"):
        resolve_command("   ", {})
    assert managed.log_tail() == ""


def test_commands_resolve_through_the_given_path() -> None:
    command = resolve_command("python -V", {"PATH": str(Path(sys.executable).parent)})

    assert Path(command[0]).parent == Path(sys.executable).parent
    assert resolve_command("no-such-program --x", {"PATH": ""}) == ["no-such-program", "--x"]


def test_minimal_contract_file_exists_for_runner_tests() -> None:
    assert MINIMAL.exists()
