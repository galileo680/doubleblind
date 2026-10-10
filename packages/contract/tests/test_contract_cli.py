import json
from pathlib import Path

from contract_fixtures import FIXTURES_DIR, PARCELLY_BINDING, SIGNED, SPOKES_BINDING
from typer.testing import CliRunner

from doubleblind_contract.cli import app

runner = CliRunner()


def test_valid_contract_exits_with_zero() -> None:
    result = runner.invoke(app, ["validate", str(SIGNED)])

    assert result.exit_code == 0
    assert result.output.strip() == "OK: 0 error(s), 0 warning(s)"


def test_broken_contract_exits_with_two_and_explains_why() -> None:
    result = runner.invoke(
        app, ["validate", str(FIXTURES_DIR / "broken_rule2_unknown_operation.yaml")]
    )

    assert result.exit_code == 2
    assert "[rule 2] scenarios[0].steps[2].expect_received.operation" in result.output
    assert "INVALID: 1 error(s), 0 warning(s)" in result.output


def test_warnings_do_not_fail_validation() -> None:
    result = runner.invoke(
        app, ["validate", str(FIXTURES_DIR / "warning_rule5_uncovered_decision.yaml")]
    )

    assert result.exit_code == 0
    assert "warning [rule 5]" in result.output


def test_json_output_is_machine_readable() -> None:
    result = runner.invoke(
        app, ["validate", "--json", str(FIXTURES_DIR / "broken_rule4_duplicate_ids.yaml")]
    )

    report = json.loads(result.output)
    assert result.exit_code == 2
    assert report["ok"] is False
    assert report["findings"][0]["rule"] == "rule 4"


def test_bindings_are_used_for_action_checks() -> None:
    result = runner.invoke(
        app,
        ["validate", str(SIGNED), "-b", str(SPOKES_BINDING), "--binding", str(PARCELLY_BINDING)],
    )

    assert result.exit_code == 0


def test_broken_binding_is_reported_with_its_file_name(tmp_path: Path) -> None:
    broken = tmp_path / "shop.binding.yaml"
    broken.write_text(
        SPOKES_BINDING.read_text(encoding="utf-8").replace("side: consumer", "side: shop"),
        encoding="utf-8",
    )

    result = runner.invoke(app, ["validate", str(SIGNED), "--binding", str(broken)])

    assert result.exit_code == 2
    assert "[binding] shop.binding.yaml: side:" in result.output


def test_missing_files_are_reported(tmp_path: Path) -> None:
    result = runner.invoke(
        app,
        ["validate", str(tmp_path / "nope.yaml"), "--binding", str(tmp_path / "nope.binding.yaml")],
    )

    assert result.exit_code == 2
    assert "[binding]" in result.output
    assert "[file]" in result.output
    assert "cannot read file" in result.output


def test_running_without_a_command_shows_help() -> None:
    result = runner.invoke(app, [])

    assert "validate" in result.output
