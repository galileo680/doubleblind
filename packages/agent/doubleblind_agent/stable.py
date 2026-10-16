import re

VOLATILE = (
    (re.compile(r"\x1b\[[0-9;]*[A-Za-z]"), ""),
    (
        re.compile(r'(File \\?"(?:<frozen [^>]+>|/usr/local/lib/python3[^"\\]*)\\?", line )\d+'),
        r"\1<n>",
    ),
    (re.compile(r"\b(127\.0\.0\.1|localhost|0\.0\.0\.0):\d+"), r"\1:<port>"),
    (
        re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d+)?(?:Z|[+-]\d{2}:?\d{2})?"),
        "<time>",
    ),
    (re.compile(r"\b\d{2}:\d{2}:\d{2}(?:[.,]\d+)?\b"), "<time>"),
    (re.compile(r"\b\d+(?:\.\d+)?\s?(?:ms|s)\b"), "<duration>"),
    (re.compile(r'"duration_ms": \d+'), '"duration_ms": "<duration>"'),
    (re.compile(r"process \[\d+\]"), "process [<pid>]"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b"), "0x<address>"),
    (re.compile(r"sha256=[0-9a-f]{64}"), "sha256=<hmac>"),
    (re.compile(r"\bses_[0-9a-f]{6,}\b"), "ses_<id>"),
    (re.compile(r"\b\d+(?:\.\d+)?%"), "<percent>"),
)


TEST_FILE_LINE = re.compile(r"^\s*[\u2713\u2717\u00d7\u276f\u2193] ")


def sorted_test_file_lines(text: str) -> str:
    lines, block, result = text.split("\n"), [], []
    for line in [*lines, ""]:
        if TEST_FILE_LINE.match(line):
            block.append(line)
            continue
        result += sorted(block)
        block = []
        result.append(line)
    return "\n".join(result[:-1])


def stable(text: str) -> str:
    for pattern, replacement in VOLATILE:
        text = pattern.sub(replacement, text)
    return sorted_test_file_lines(text)
