import ast
import io
import json
import re
import shutil
import subprocess
import sys
import tokenize
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
JS_SCANNER = ROOT / "scripts" / "find-js-comments.mjs"
EXCLUDED_PARTS = {
    ".git",
    ".venv",
    "node_modules",
    "dist",
    "volumes",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".claude",
    "ideas",
    "docs",
    "recordings",
}
HASH_SUFFIXES = {".toml", ".sh", ".cfg", ".ini", ".conf"}
HASH_NAMES = {
    "Makefile",
    "Dockerfile",
    ".gitignore",
    ".gitattributes",
    ".dockerignore",
    ".prettierignore",
    ".editorconfig",
    ".env.example",
}
YAML_SUFFIXES = {".yaml", ".yml"}
SCRIPT_SUFFIXES = {".js", ".mjs", ".cjs", ".jsx", ".ts", ".tsx", ".mts", ".cts"}
CSS_COMMENT = re.compile(r"/\*")
HTML_COMMENT = re.compile(r"<!--")


@dataclass(frozen=True)
class Finding:
    path: Path
    line: int

    def render(self) -> str:
        return f"{self.path.as_posix()}:{self.line}: comment found"


def candidate_files(root: Path) -> list[Path]:
    git = shutil.which("git")
    if git and (root / ".git").exists():
        listing = subprocess.run(
            [git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=root,
            capture_output=True,
            check=True,
        )
        paths = [root / item for item in listing.stdout.decode("utf-8").split("\0") if item]
    else:
        paths = [path for path in root.rglob("*") if path.is_file()]
    return sorted(path for path in paths if path.is_file() and not is_excluded(path, root))


def is_excluded(path: Path, root: Path) -> bool:
    return any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts)


def uses_hash_comments(path: Path) -> bool:
    name = path.name
    return (
        path.suffix in HASH_SUFFIXES
        or name in HASH_NAMES
        or name.startswith("Dockerfile.")
        or (name.startswith("requirements") and path.suffix == ".txt")
    )


def python_comment_lines(text: str) -> list[int]:
    lines: set[int] = set()
    for token in tokenize.generate_tokens(io.StringIO(text).readline):
        if token.type != tokenize.COMMENT:
            continue
        line = token.start[0]
        if line == 1 and token.string.startswith("#!"):
            continue
        lines.add(line)
    for node in ast.walk(ast.parse(text)):
        if (
            isinstance(node, ast.Expr)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            lines.add(node.lineno)
    return sorted(lines)


def hash_comment_lines(text: str) -> list[int]:
    lines: list[int] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if number == 1 and line.startswith("#!"):
            continue
        if line.lstrip().startswith("#"):
            lines.append(number)
    return lines


def collect_comment_tokens(value: object) -> Iterator[object]:
    if value is None:
        return
    if type(value).__name__ == "CommentToken":
        yield value
        return
    if isinstance(value, list | tuple):
        for item in value:
            yield from collect_comment_tokens(item)


def yaml_node_comments(node: object) -> Iterator[object]:
    attributes = getattr(node, "ca", None)
    if attributes is not None:
        yield from collect_comment_tokens(attributes.comment)
        for item in attributes.items.values():
            yield from collect_comment_tokens(item)
        yield from collect_comment_tokens(getattr(attributes, "end", None))
    if isinstance(node, dict):
        for child in node.values():
            yield from yaml_node_comments(child)
    elif isinstance(node, list):
        for child in node:
            yield from yaml_node_comments(child)


def yaml_comment_lines(text: str) -> list[int]:
    from ruamel.yaml import YAML

    lines = set(hash_comment_lines(text))
    for document in YAML(typ="rt").load_all(text):
        for token in yaml_node_comments(document):
            mark = getattr(token, "start_mark", None)
            first_line = str(getattr(token, "value", "")).split("\n", 1)[0]
            if mark is not None and first_line.strip().startswith("#"):
                lines.add(mark.line + 1)
    return sorted(lines)


def pattern_lines(text: str, pattern: re.Pattern[str]) -> list[int]:
    return sorted({text.count("\n", 0, match.start()) + 1 for match in pattern.finditer(text)})


def script_comment_findings(paths: Iterable[Path], root: Path) -> list[Finding]:
    files = list(paths)
    if not files:
        return []
    node = shutil.which("node")
    if node is None:
        raise SystemExit("node is required to scan JavaScript and TypeScript files.")
    completed = subprocess.run(
        [node, str(JS_SCANNER), *(str(path) for path in files)],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stderr.strip() or "JavaScript comment scanner failed.")
    report: dict[str, list[int]] = json.loads(completed.stdout or "{}")
    return [Finding(Path(file), line) for file, lines in report.items() for line in lines]


def text_findings(path: Path) -> list[Finding]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".py":
        lines = python_comment_lines(text)
    elif path.suffix in YAML_SUFFIXES:
        lines = yaml_comment_lines(text)
    elif uses_hash_comments(path):
        lines = hash_comment_lines(text)
    elif path.suffix == ".css":
        lines = pattern_lines(text, CSS_COMMENT)
    elif path.suffix == ".html":
        lines = pattern_lines(text, HTML_COMMENT)
    else:
        lines = []
    return [Finding(path, line) for line in lines]


def find_comments(root: Path, paths: Iterable[Path] | None = None) -> list[Finding]:
    files = list(paths) if paths is not None else candidate_files(root)
    scripts = [path for path in files if path.suffix in SCRIPT_SUFFIXES]
    findings = [finding for path in files if path not in scripts for finding in text_findings(path)]
    findings += script_comment_findings(scripts, root)
    return sorted(findings, key=lambda finding: (finding.path.as_posix(), finding.line))


def main() -> int:
    findings = find_comments(ROOT)
    for finding in findings:
        relative = Finding(finding.path.resolve().relative_to(ROOT), finding.line)
        print(relative.render())
    if findings:
        print(f"{len(findings)} comment(s) found. Comments in code are not allowed.")
        return 1
    print("No comments found in code.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
