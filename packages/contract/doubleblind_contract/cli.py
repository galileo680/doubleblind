import json
from pathlib import Path
from typing import Annotated

import typer

from doubleblind_contract.models import Binding
from doubleblind_contract.validate import (
    RULE_BINDING,
    ValidationReport,
    check_binding_text,
    validate_contract_text,
)

EXIT_OK = 0
EXIT_INVALID = 2

app = typer.Typer(help="DoubleBlind contract tools.", no_args_is_help=True, add_completion=False)


@app.callback()
def main_callback() -> None:
    return None


def read_text(path: Path, report: ValidationReport, rule: str) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as error:
        report.error(rule, str(path), f"cannot read file: {error.strerror or error}")
        return None


def load_bindings(paths: list[Path], report: ValidationReport) -> list[Binding]:
    bindings: list[Binding] = []
    for path in paths:
        text = read_text(path, report, RULE_BINDING)
        if text is None:
            continue
        binding, binding_report = check_binding_text(text)
        for finding in binding_report.findings:
            location = f"{path.name}: {finding.path}" if finding.path else path.name
            report.error(finding.rule, location, finding.message)
        if binding is not None:
            bindings.append(binding)
    return bindings


def print_report(report: ValidationReport, as_json: bool) -> None:
    if as_json:
        typer.echo(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        return
    for finding in report.findings:
        typer.echo(finding.render())
    status = "OK" if report.ok else "INVALID"
    typer.echo(f"{status}: {len(report.errors)} error(s), {len(report.warnings)} warning(s)")


@app.command()
def validate(
    contract: Annotated[Path, typer.Argument(help="Contract YAML file")],
    binding: Annotated[
        list[Path] | None,
        typer.Option("--binding", "-b", help="Binding YAML file (repeatable)"),
    ] = None,
    as_json: Annotated[bool, typer.Option("--json", help="Print the report as JSON")] = False,
) -> None:
    report = ValidationReport()
    bindings = load_bindings(binding or [], report)
    text = read_text(contract, report, "file")
    if text is not None:
        report.findings.extend(validate_contract_text(text, bindings).findings)
    print_report(report, as_json)
    raise typer.Exit(EXIT_OK if report.ok else EXIT_INVALID)


def main() -> None:
    app()
