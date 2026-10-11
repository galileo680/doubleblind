import hashlib
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from doubleblind_llm.domain import RawCompletion

CASSETTE_ENV = "DOUBLEBLIND_CASSETTE"
TAIL_CHARS = 4000
SHORTLIST = 5
SHOWN_CHANGES = 3
CONTEXT_CHARS = 120


def fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def request_tail(payload: dict[str, Any]) -> str:
    messages = payload.get("messages") or []
    if not messages:
        return ""
    content = messages[-1].get("content")
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return text[-TAIL_CHARS:]


@dataclass(frozen=True)
class RecordedRequest:
    role: str
    party: str | None
    tail: str


class Cassette:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._recorded: dict[str, list[RawCompletion]] | None = None
        self._requests: list[RecordedRequest] = []
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
                    if item.get("tail"):
                        self._requests.append(
                            RecordedRequest(item["role"], item.get("party"), item["tail"])
                        )
            self._recorded = recorded
        return self._recorded

    def next(self, key: str) -> RawCompletion | None:
        entries = self._load().get(key, [])
        position = self._cursor[key]
        if not entries:
            return None
        self._cursor[key] = position + 1
        return entries[min(position, len(entries) - 1)]

    def next_unused(self, key: str) -> RawCompletion | None:
        entries = self._load().get(key, [])
        position = self._cursor[key]
        if position >= len(entries):
            return None
        self._cursor[key] = position + 1
        return entries[position]

    def record(
        self,
        key: str,
        role: str,
        completion: RawCompletion,
        party: str | None = None,
        tail: str = "",
    ) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(
            {
                "key": key,
                "role": role,
                "party": party,
                "tail": tail,
                "completion": completion.model_dump(mode="json"),
            },
            ensure_ascii=False,
        )
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        self._load()[key].append(completion)
        self._cursor[key] = len(self._load()[key])
        if tail:
            self._requests.append(RecordedRequest(role, party, tail))

    def miss_report(self, role: str, party: str | None, tail: str) -> str:
        self._load()
        candidates = [item for item in self._requests if item.role == role and item.party == party]
        if not candidates:
            return f"the cassette has no recorded {role} requests of {party} with their context"
        shortlist = sorted(
            candidates,
            key=lambda item: SequenceMatcher(None, item.tail, tail).quick_ratio(),
            reverse=True,
        )[:SHORTLIST]
        best = max(shortlist, key=lambda item: SequenceMatcher(None, item.tail, tail).ratio())
        matcher = SequenceMatcher(None, best.tail, tail)
        changes = [
            f"recorded: ...{best.tail[max(0, i1 - CONTEXT_CHARS) : i2 + CONTEXT_CHARS]}...\n"
            f"now:      ...{tail[max(0, j1 - CONTEXT_CHARS) : j2 + CONTEXT_CHARS]}..."
            for tag, i1, i2, j1, j2 in matcher.get_opcodes()
            if tag != "equal"
        ]
        return (
            f"closest recorded {role} request of {party} is {matcher.ratio():.1%} similar; "
            "differences in its last message:\n" + "\n".join(changes[:SHOWN_CHANGES])
        )


def cassette_path_from_env(repo_root: Path) -> Path:
    configured = os.environ.get(CASSETTE_ENV)
    return Path(configured) if configured else repo_root / "demo" / "recordings" / "default.jsonl"
