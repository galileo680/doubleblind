import json
import os
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Annotated

import typer

from doubleblind_contract.loader import ContractLoadError, load_binding, load_contract
from doubleblind_contract.models import Binding
from doubleblind_contract.report import render_console, write_json, write_junit
from doubleblind_contract.runner import run_consumer_tests, run_e2e_tests, run_provider_tests
from doubleblind_contract.runtime import ConfigurationError, RunReport, load_secrets, read_env_file
from doubleblind_contract.stub_server import ProviderStub
from doubleblind_contract.validate import (
    RULE_BINDING,
    ValidationReport,
    check_binding_text,
    validate_contract_text,
)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_INVALID = 2

app = typer.Typer(help="DoubleBlind contract tools.", no_args_is_help=True, add_completion=False)
test_app = typer.Typer(help="Run the contract tests of one side.", no_args_is_help=True)
app.add_typer(test_app, name="test")

ScenariosOption = Annotated[
    str | None, typer.Option("--scenarios", help="Comma-separated scenario ids, e.g. S1,S2")
]
ReportOption = Annotated[Path | None, typer.Option("--report", help="Write the JSON report here")]
JunitOption = Annotated[Path | None, typer.Option("--junit", help="Write a JUnit XML report here")]


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


def parse_scenarios(value: str | None) -> list[str] | None:
    if not value:
        return None
    return [item.strip() for item in value.split(",") if item.strip()]


def execute(
    run: Callable[[], RunReport], report_path: Path | None, junit_path: Path | None
) -> None:
    try:
        report = run()
    except (ConfigurationError, ContractLoadError) as error:
        for line in str(error).splitlines():
            typer.echo(f"configuration error: {line}", err=True)
        raise typer.Exit(EXIT_INVALID) from error
    for line in render_console(report):
        typer.echo(line)
    if report_path is not None:
        write_json(report, report_path)
    if junit_path is not None:
        write_junit(report, junit_path)
    raise typer.Exit(EXIT_OK if report.ok else EXIT_FAILED)


@test_app.command("consumer")
def test_consumer(
    contract: Annotated[Path, typer.Argument(help="Contract YAML file")],
    binding: Annotated[Path, typer.Option("--binding", "-b", help="Consumer binding YAML file")],
    scenarios: ScenariosOption = None,
    report: ReportOption = None,
    junit: JunitOption = None,
) -> None:
    execute(
        lambda: run_consumer_tests(contract, binding, parse_scenarios(scenarios), env=os.environ),
        report,
        junit,
    )


@test_app.command("provider")
def test_provider(
    contract: Annotated[Path, typer.Argument(help="Contract YAML file")],
    binding: Annotated[Path, typer.Option("--binding", "-b", help="Provider binding YAML file")],
    scenarios: ScenariosOption = None,
    report: ReportOption = None,
    junit: JunitOption = None,
) -> None:
    execute(
        lambda: run_provider_tests(contract, binding, parse_scenarios(scenarios), env=os.environ),
        report,
        junit,
    )


@app.command("e2e")
def e2e(
    contract: Annotated[Path, typer.Argument(help="Contract YAML file")],
    consumer_binding: Annotated[Path, typer.Option("--consumer-binding", help="Consumer binding")],
    provider_binding: Annotated[Path, typer.Option("--provider-binding", help="Provider binding")],
    start_apps: Annotated[
        bool, typer.Option("--start-apps", help="Start both apps from their bindings")
    ] = False,
    scenarios: ScenariosOption = None,
    report: ReportOption = None,
    junit: JunitOption = None,
) -> None:
    execute(
        lambda: run_e2e_tests(
            contract,
            consumer_binding,
            provider_binding,
            parse_scenarios(scenarios),
            env=os.environ,
            start_apps=start_apps,
        ),
        report,
        junit,
    )


@app.command("stub")
def stub(
    contract: Annotated[Path, typer.Argument(help="Contract YAML file")],
    port: Annotated[int, typer.Option("--port", help="Port to listen on")] = 9100,
    binding: Annotated[
        Path | None, typer.Option("--binding", "-b", help="Consumer binding (for webhooks)")
    ] = None,
    secrets_env_file: Annotated[
        Path | None, typer.Option("--secrets-env-file", help="KEY=VALUE file with secrets")
    ] = None,
) -> None:
    env = dict(os.environ)
    try:
        if secrets_env_file is not None:
            env.update(read_env_file(secrets_env_file))
        loaded = load_contract(contract)
        secrets = load_secrets(loaded, env)
        receiver = load_binding(binding, env=env).webhook_receiver_url if binding else None
    except (ConfigurationError, ContractLoadError, OSError) as error:
        typer.echo(f"configuration error: {error}", err=True)
        raise typer.Exit(EXIT_INVALID) from error
    provider_stub = ProviderStub(loaded, secrets, receiver, port=port)
    provider_stub.start()
    typer.echo(f"stub for {loaded.id} v{loaded.version} listening on {provider_stub.url}")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        provider_stub.stop()


def main() -> None:
    app()
