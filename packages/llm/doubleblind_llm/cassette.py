import hashlib
import json
import os
from collections import defaultdict
from pathlib import Path
from typing import Any

from doubleblind_llm.domain import RawCompletion

CASSETTE_ENV = "DOUBLEBLIND_CASSETTE"


def fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Cassette:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._recorded: dict[str, list[RawCompletion]] | None = None
        self._cursor: dict[str, int] = defaultdict(int)

    def _load(self) -> dict[str, list[RawCompletion]]:
        if self._recorded is None:
            recorded: dict[str, list[RawCompletion]] = defaultdict(list)
            if self.path.is_file():
                for line in self.path.read_text(encoding="utf-8").splitlines():
                    if not line.strip():
                        continue
                    item = json.loads(line)
                    recorded[item["key"]].append(RawCompletion.model_validate(item["completion"]))
            self._recorded = recorded
        return self._recorded

    def next(self, key: str) -> RawCompletion | None:
        entries = self._load().get(key, [])
        position = self._cursor[key]
        if not entries:
            return None
        self._cursor[key] = position + 1
        return entries[min(position, len(entries) - 1)]

    def record(self, key: str, role: str, completion: RawCompletion) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {"key": key, "role": role, "completion": completion.model_dump(mode="json")},
            ensure_ascii=False,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        self._load()[key].append(completion)
        self._cursor[key] = len(self._load()[key])


def cassette_path_from_env(repo_root: Path) -> Path:
    configured = os.environ.get(CASSETTE_ENV)
    return Path(configured) if configured else repo_root / "demo" / "recordings" / "default.jsonl"
