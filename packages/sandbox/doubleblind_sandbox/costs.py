from collections.abc import Callable
from pathlib import Path

from doubleblind_llm.budget import (
    BudgetGuard,
    Ledger,
    LedgerEntry,
    ledger_path_from_env,
    spend_limit_from_env,
)

SANDBOX_MODEL = "token-factory-sandboxes"
MINIMUM_RESERVE_USD = 0.25

CostListener = Callable[[str, float, str], None]


class LedgerCosts:
    def __init__(self, ledger: Ledger, limit_usd: float, session_id: str | None = None) -> None:
        self.ledger = ledger
        self.guard = BudgetGuard(ledger, limit_usd)
        self.session_id = session_id

    @classmethod
    def from_env(cls, repo_root: Path, session_id: str | None = None) -> "LedgerCosts":
        return cls(Ledger(ledger_path_from_env(repo_root)), spend_limit_from_env(), session_id)

    def check(self) -> None:
        self.guard.check(MINIMUM_RESERVE_USD)

    def record(self, party: str, cost_usd: float, note: str) -> None:
        if cost_usd <= 0:
            return
        self.ledger.append(
            LedgerEntry.now(
                role=f"sandbox:{party}",
                model=SANDBOX_MODEL,
                prompt_tokens=0,
                completion_tokens=0,
                cost_usd=cost_usd,
                party=party,
                session_id=self.session_id,
                note=note[:200],
            )
        )
