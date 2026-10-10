import asyncio

import typer

from doubleblind_llm.budget import Ledger, ledger_path_from_env, spend_limit_from_env
from doubleblind_llm.capabilities import CapabilityReport, save_capabilities
from doubleblind_llm.client import LLMClient
from doubleblind_llm.doctor import Doctor
from doubleblind_llm.errors import LLMError
from doubleblind_llm.settings import load_dotenv, load_settings

app = typer.Typer(no_args_is_help=True, add_completion=False, help="DoubleBlind LLM tools")


def yes_no(value: bool) -> str:
    return "yes" if value else "no"


def render_report(report: CapabilityReport) -> str:
    lines = ["Token Factory capabilities", ""]
    for alias, model in report.models.items():
        latency = f"{model.latency_ms} ms" if model.latency_ms is not None else "-"
        lines += [
            f"{alias} ({model.id})",
            f"  available        {yes_no(model.available)}",
            f"  thinking toggle  {model.thinking.toggle}",
            f"  native tools     {yes_no(model.native_tools)}",
            f"  json_schema      {yes_no(model.json_schema)}",
            f"  latency          {latency}",
            *[f"  error            {error}" for error in model.errors],
            "",
        ]
    return "\n".join(lines).rstrip()


def render_breakdown(title: str, totals: dict[str, float]) -> str:
    width = max((len(name) for name in totals), default=0)
    rows = [f"  {name.ljust(width)}  ${amount:.6f}" for name, amount in totals.items()]
    return "\n".join([title, *rows])


@app.command()
def doctor() -> None:
    client = LLMClient.from_env(mode="live")
    ledger = client.ledger
    spent_before = ledger.total_usd() if ledger else 0.0

    async def execute() -> CapabilityReport:
        try:
            return await Doctor(client).run()
        finally:
            await client.aclose()

    try:
        report = asyncio.run(execute())
    except LLMError as error:
        typer.echo(str(error), err=True)
        raise typer.Exit(code=1) from error
    target = client.settings.config_dir / "capabilities.json"
    save_capabilities(target, report)
    typer.echo(render_report(report))
    spent = (ledger.total_usd() if ledger else 0.0) - spent_before
    typer.echo(f"\nReport written to {target}. Doctor spent ${spent:.6f}.")
    if not all(item.available for item in report.models.values()):
        raise typer.Exit(code=1)


@app.command()
def budget() -> None:
    settings = load_settings()
    load_dotenv(settings.repo_root / ".env")
    ledger = Ledger(ledger_path_from_env(settings.repo_root))
    limit = spend_limit_from_env()
    spent = ledger.total_usd()
    typer.echo(f"Ledger: {ledger.path}")
    typer.echo(
        f"Spent ${spent:.6f} of ${limit:.2f} limit, remaining ${limit - spent:.6f} "
        f"({len(ledger.entries())} calls)"
    )
    for field in ("role", "model"):
        typer.echo("")
        typer.echo(render_breakdown(f"Spend by {field}", ledger.breakdown(field)))


def main() -> None:
    app()


if __name__ == "__main__":
    main()
