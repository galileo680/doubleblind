import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel

from doubleblind_llm.errors import BudgetExceededError, ConfigurationError

LEDGER_ENV = "DOUBLEBLIND_LEDGER_PATH"
SPEND_LIMIT_ENV = "DOUBLEBLIND_SPEND_LIMIT_USD"
DEFAULT_SPEND_LIMIT_USD = 50.0


class LedgerEntry(BaseModel):
    ts: str
    role: str
    model: str
    prompt_tokens: int
    completion_tokens: int
    cost_usd: float
    session_id: str | None = None
    party: str | None = None
    cache_key: str | None = None
    note: str | None = None

    @classmethod
    def now(cls, **fields: object) -> "LedgerEntry":
        return cls.model_validate({"ts": datetime.now(UTC).isoformat(), **fields})


class Ledger:
    def __init__(self, path: Path) -> None:
        self.path = path

    def append(self, entry: LedgerEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.model_dump_json() + "\n")

    def entries(self) -> list[LedgerEntry]:
        if not self.path.is_file():
            return []
        return [
            LedgerEntry.model_validate(json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def total_usd(self) -> float:
        return sum(entry.cost_usd for entry in self.entries())

    def breakdown(self, field: str) -> dict[str, float]:
        totals: dict[str, float] = defaultdict(float)
        for entry in self.entries():
            totals[str(getattr(entry, field) or "-")] += entry.cost_usd
        return dict(sorted(totals.items(), key=lambda item: item[1], reverse=True))


class BudgetGuard:
    def __init__(self, ledger: Ledger, limit_usd: float) -> None:
        self.ledger = ledger
        self.limit_usd = limit_usd

    def check(self, estimate_usd: float, reserved_usd: float = 0.0) -> None:
        spent = self.ledger.total_usd() + reserved_usd
        if spent + estimate_usd > self.limit_usd:
            raise BudgetExceededError(spent, estimate_usd, self.limit_usd)

    def remaining_usd(self) -> float:
        return self.limit_usd - self.ledger.total_usd()


def ledger_path_from_env(repo_root: Path) -> Path:
    configured = os.environ.get(LEDGER_ENV)
    return Path(configured) if configured else repo_root / "volumes" / "llm-ledger.jsonl"


def spend_limit_from_env() -> float:
    raw = os.environ.get(SPEND_LIMIT_ENV, str(DEFAULT_SPEND_LIMIT_USD))
    try:
        value = float(raw)
    except ValueError as error:
        raise ConfigurationError(f"{SPEND_LIMIT_ENV} must be a number, got '{raw}'") from error
    if value <= 0:
        raise ConfigurationError(f"{SPEND_LIMIT_ENV} must be positive, got {value}")
    return value
