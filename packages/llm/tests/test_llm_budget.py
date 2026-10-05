import pytest
from llm_helpers import ClientFactory, FakeTokenFactory, completion_body
from typer.testing import CliRunner

from doubleblind_llm import cli
from doubleblind_llm.budget import BudgetGuard, Ledger, LedgerEntry
from doubleblind_llm.domain import LLMRequest, Role
from doubleblind_llm.errors import BudgetExceededError


def entry(cost: float, role: str = "negotiator", model: str = "ultra") -> LedgerEntry:
    return LedgerEntry.now(
        role=role, model=model, prompt_tokens=1, completion_tokens=1, cost_usd=cost
    )


def test_ledger_totals_and_breakdown(ledger: Ledger) -> None:
    ledger.append(entry(0.5, role="negotiator"))
    ledger.append(entry(0.25, role="coder", model="super"))
    ledger.append(entry(0.25, role="coder", model="super"))
    assert ledger.total_usd() == pytest.approx(1.0)
    assert ledger.breakdown("role") == {"negotiator": 0.5, "coder": 0.5}
    assert ledger.breakdown("model") == {"ultra": 0.5, "super": 0.5}


def test_guard_rejects_spend_over_limit(ledger: Ledger) -> None:
    ledger.append(entry(9.0))
    guard = BudgetGuard(ledger, 10.0)
    guard.check(0.9)
    with pytest.raises(BudgetExceededError):
        guard.check(1.5)
    with pytest.raises(BudgetExceededError):
        guard.check(0.5, reserved_usd=0.6)
    assert guard.remaining_usd() == pytest.approx(1.0)


async def test_client_blocks_live_call_before_sending_request(
    make_client: ClientFactory, api: FakeTokenFactory, ledger: Ledger
) -> None:
    api.reply(completion_body("ok"))
    ledger.append(entry(24.99))
    request = LLMRequest(role=Role.NEGOTIATOR, messages=[{"role": "user", "content": "go"}])
    with pytest.raises(BudgetExceededError):
        await make_client(limit_usd=25.0).complete(request)
    assert api.chat_calls == 0
    assert len(ledger.entries()) == 1


def test_budget_command_reports_spend(monkeypatch: pytest.MonkeyPatch, ledger: Ledger) -> None:
    ledger.append(entry(0.002, role="negotiator"))
    ledger.append(entry(0.001, role="doctor", model="nano"))
    monkeypatch.setattr(cli, "load_dotenv", lambda path: None)
    monkeypatch.setenv("DOUBLEBLIND_LEDGER_PATH", str(ledger.path))
    monkeypatch.setenv("DOUBLEBLIND_SPEND_LIMIT_USD", "25")
    result = CliRunner().invoke(cli.app, ["budget"])
    assert result.exit_code == 0, result.output
    assert "Spent $0.003000 of $25.00 limit" in result.output
    assert "negotiator" in result.output
    assert "doctor" in result.output
