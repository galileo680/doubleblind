import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_checker() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_no_comments", ROOT / "scripts" / "check_no_comments.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


checker = load_checker()


def write(directory: Path, name: str, content: str) -> Path:
    path = directory / name
    path.write_text(content, encoding="utf-8")
    return path


def lines_for(directory: Path, path: Path) -> list[int]:
    return [finding.line for finding in checker.find_comments(directory, [path])]


def test_python_comments_are_found_but_hashes_in_strings_are_not(tmp_path: Path) -> None:
    path = write(tmp_path, "module.py", '#!/usr/bin/env python\nvalue = "#tag"\ncount = 1  # x\n')
    assert lines_for(tmp_path, path) == [3]


def test_python_docstrings_and_bare_strings_count_as_comments(tmp_path: Path) -> None:
    content = 'def run() -> None:\n    """Explain."""\n    value = "kept"\n    "note"\n'
    path = write(tmp_path, "module.py", content)
    assert lines_for(tmp_path, path) == [2, 4]


def test_yaml_full_line_and_inline_comments_are_found(tmp_path: Path) -> None:
    content = 'name: demo\n# heading\nurl: "http://x/#/schemas/A"\nport: 80  # inline\n'
    path = write(tmp_path, "config.yaml", content)
    assert lines_for(tmp_path, path) == [2, 4]


def test_hash_comment_files_are_checked(tmp_path: Path) -> None:
    makefile = write(tmp_path, "Makefile", "# build\nall:\n\techo ok\n")
    script = write(tmp_path, "run.sh", "#!/bin/sh\necho ok\n")
    assert lines_for(tmp_path, makefile) == [1]
    assert lines_for(tmp_path, script) == []


def test_css_and_html_comments_are_found(tmp_path: Path) -> None:
    css = write(tmp_path, "style.css", "body {\n  color: red;\n}\n/* note */\n")
    html = write(tmp_path, "index.html", "<html>\n<!-- note -->\n</html>\n")
    assert lines_for(tmp_path, css) == [4]
    assert lines_for(tmp_path, html) == [2]


typescript_installed = (ROOT / "dashboard" / "node_modules" / "typescript").exists()


@pytest.mark.skipif(not typescript_installed, reason="dashboard dependencies are not installed")
def test_script_comments_are_found_without_false_positives(tmp_path: Path) -> None:
    content = (
        "const pattern = /https?:\\/\\//;\n"
        'const url = "http://example.com";\n'
        "// note\n"
        "const total = 1; /* trailing */\n"
    )
    path = write(tmp_path, "module.ts", content)
    assert lines_for(tmp_path, path) == [3, 4]
