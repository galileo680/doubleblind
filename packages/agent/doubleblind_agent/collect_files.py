import json
import os
from pathlib import Path

SKIPPED_DIRS = {
    "node_modules",
    ".git",
    ".doubleblind",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    ".venv",
}
SKIPPED_NAMES = {"package-lock.json", "yarn.lock", "poetry.lock", "uv.lock"}
SKIPPED_SUFFIXES = (".db", ".sqlite", ".pyc")
MAX_FILE_BYTES = 200_000
TOKEN_BUDGET = 600_000
CHARS_PER_TOKEN = 3.5
DOC_DIRS = ("docs/", "internal-wiki/", "public-docs/")


def priority(path: str) -> int:
    if path.startswith(DOC_DIRS) or path.endswith(".md"):
        return 0
    if "test" in path:
        return 2
    return 1


def text_of(path: Path) -> str | None:
    if path.stat().st_size > MAX_FILE_BYTES:
        return None
    data = path.read_bytes()
    if b"\0" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        return None


def main() -> None:
    root = Path(".")
    tree: list[str] = []
    for directory, dirs, names in os.walk(root):
        dirs[:] = sorted(name for name in dirs if name not in SKIPPED_DIRS)
        for name in sorted(names):
            if name in SKIPPED_NAMES or name.endswith(SKIPPED_SUFFIXES):
                continue
            tree.append((Path(directory) / name).relative_to(root).as_posix())
    files = []
    budget = TOKEN_BUDGET * CHARS_PER_TOKEN
    for path in sorted(tree, key=lambda item: (priority(item), item)):
        content = text_of(root / path)
        if content is None or len(content) > budget:
            continue
        budget -= len(content)
        files.append({"path": path, "content": content})
    print(json.dumps({"tree": tree, "files": files}))


if __name__ == "__main__":
    main()
