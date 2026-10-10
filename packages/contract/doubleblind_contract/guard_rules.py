import math
import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

CODE_FENCE = "```"
CODE_LINE = re.compile(
    r"^\s*(?:(?:def|class|import|function|const|let|var|return|export|await)\b"
    r"|from\s+\S+\s+import\b|if\s*\(|for\s*\(|async\s+def\b)"
    r"|=>|;\s*$|\)\s*\{\s*$"
)
SECRET_PATTERNS = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"NEBIUS_API_KEY"),
)
TOKEN_CANDIDATE = re.compile(r"[A-Za-z0-9+=_-]{32,}")
HEX = re.compile(r"[0-9a-fA-F]+")
WORD_SEPARATOR = re.compile(r"[_-]")
MIN_RANDOM_SEGMENT = 20
DIGIT = re.compile(r"\d")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
PHONE = re.compile(r"(?<![\w+])(?:\+48|0048)?[ -]?\d{3}[ -]?\d{3}[ -]?\d{3}(?!\w)")
STACK_TRACES = (
    re.compile(r"Traceback \(most recent call last\)"),
    re.compile(r"at .* \(.*:\d+:\d+\)"),
)
ENTROPY_LIMIT = 4.0
HEX_ENTROPY_LIMIT = 3.0
MAX_CODE_LINES = 1
CODE_BLOCK_PATCH_PREFIXES = ("/schemas", "/operations", "/webhooks", "/scenarios")

FieldPath = tuple[str | int, ...]


def normalize_phone(text: str) -> str:
    digits = re.sub(r"\D", "", text)
    if len(digits) == 13 and digits.startswith("0048"):
        return digits[4:]
    if len(digits) == 11 and digits.startswith("48"):
        return digits[2:]
    return digits


@dataclass(frozen=True)
class SyntheticAllowlist:
    phones: frozenset[str] = frozenset()
    emails: frozenset[str] = frozenset()

    @classmethod
    def build(cls, phones: Iterable[str] = (), emails: Iterable[str] = ()) -> "SyntheticAllowlist":
        return cls(
            frozenset(normalize_phone(phone) for phone in phones),
            frozenset(email.lower() for email in emails),
        )


@dataclass
class GuardVerdict:
    reasons: list[str] = field(default_factory=list)
    code_lines: int = 0
    redactions: list[tuple[str, str]] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        return bool(self.reasons)

    def merge(self, other: "GuardVerdict") -> None:
        self.reasons += other.reasons
        self.code_lines += other.code_lines
        self.redactions += other.redactions


def shannon_entropy(text: str) -> float:
    counts = Counter(text)
    total = len(text)
    return -sum(count / total * math.log2(count / total) for count in counts.values())


def code_line_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if CODE_LINE.search(line))


def describe(path: FieldPath) -> str:
    rendered = ""
    for part in path:
        rendered += f"[{part}]" if isinstance(part, int) else (f".{part}" if rendered else part)
    return rendered or "text"


def check_text(
    text: str,
    path: FieldPath,
    allowlist: SyntheticAllowlist,
    allow_code: bool = False,
    repo_paths: Sequence[str] = (),
) -> GuardVerdict:
    verdict = GuardVerdict()
    where = describe(path)
    if not allow_code:
        if CODE_FENCE in text:
            verdict.reasons.append(f"{where}: code blocks are not allowed")
        lines = code_line_count(text)
        if lines > MAX_CODE_LINES:
            verdict.reasons.append(f"{where}: looks like source code ({lines} code lines)")
        else:
            verdict.code_lines += lines
    for pattern in SECRET_PATTERNS:
        if pattern.search(text):
            verdict.reasons.append(f"{where}: contains a secret ({pattern.pattern[:24]}...)")
    for candidate in TOKEN_CANDIDATE.findall(text):
        if max(len(segment) for segment in WORD_SEPARATOR.split(candidate)) < MIN_RANDOM_SEGMENT:
            continue
        limit = HEX_ENTROPY_LIMIT if HEX.fullmatch(candidate) else ENTROPY_LIMIT
        if DIGIT.search(candidate) and shannon_entropy(candidate) > limit:
            verdict.reasons.append(f"{where}: contains a high-entropy token")
            break
    for email in EMAIL.findall(text):
        if email.lower() not in allowlist.emails:
            verdict.reasons.append(f"{where}: contains a personal e-mail address")
            break
    for phone in PHONE.findall(text):
        if normalize_phone(phone) not in allowlist.phones:
            verdict.reasons.append(f"{where}: contains a phone number outside the synthetic data")
            break
    for pattern in STACK_TRACES:
        if pattern.search(text):
            verdict.reasons.append(f"{where}: contains a stack trace")
            break
    for repo_path in repo_paths:
        if "/" in repo_path and repo_path in text:
            verdict.redactions.append((repo_path, "[internal]"))
    return verdict


def check_value(
    value: Any,
    path: FieldPath,
    allowlist: SyntheticAllowlist,
    allow_code: Callable[[FieldPath], bool] = lambda path: False,
    repo_paths: Sequence[str] = (),
) -> GuardVerdict:
    verdict = GuardVerdict()
    if isinstance(value, str):
        verdict.merge(check_text(value, path, allowlist, allow_code(path), repo_paths))
    elif isinstance(value, Mapping):
        for key, item in value.items():
            verdict.merge(check_value(item, (*path, str(key)), allowlist, allow_code, repo_paths))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            verdict.merge(check_value(item, (*path, index), allowlist, allow_code, repo_paths))
    return verdict


def patch_allows_code(patches: Any) -> Callable[[FieldPath], bool]:
    def allowed(path: FieldPath) -> bool:
        if len(path) < 3 or path[0] != "patches" or path[2] != "value":
            return False
        index = path[1]
        if not isinstance(index, int) or not isinstance(patches, list) or index >= len(patches):
            return False
        target = patches[index].get("path", "") if isinstance(patches[index], Mapping) else ""
        return isinstance(target, str) and target.startswith(CODE_BLOCK_PATCH_PREFIXES)

    return allowed


def check_turn(
    turn: Mapping[str, Any], allowlist: SyntheticAllowlist, repo_paths: Sequence[str] = ()
) -> GuardVerdict:
    return check_value(
        dict(turn), (), allowlist, patch_allows_code(turn.get("patches")), repo_paths
    )


def check_message(
    value: Any, allowlist: SyntheticAllowlist, repo_paths: Sequence[str] = ()
) -> GuardVerdict:
    return check_value(value, (), allowlist, repo_paths=repo_paths)


def check_test_run(payload: Mapping[str, Any], allowlist: SyntheticAllowlist) -> GuardVerdict:
    return check_value(
        dict(payload), (), allowlist, lambda path: bool(path) and path[0] == "binding_yaml"
    )


def redact(text: str, redactions: Sequence[tuple[str, str]]) -> str:
    for original, replacement in redactions:
        text = text.replace(original, replacement)
    return text
