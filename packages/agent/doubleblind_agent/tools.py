import json
import posixpath
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import doubleblind_agent.contract_test_entry as contract_test_entry
from doubleblind_agent.triage import failing, triage
from doubleblind_contract.loader import parse_yaml
from doubleblind_contract.runtime import secret_env_name
from doubleblind_contract.validate import check_binding_text, validate_contract_data
from doubleblind_llm import LLMClient
from doubleblind_sandbox.provider import (
    REPO_DIR,
    ExecResult,
    SandboxError,
    ServiceSandbox,
    Workspace,
    repo_path,
)

ENTRY_SOURCE = Path(contract_test_entry.__file__).read_bytes()
WORK_DIR = ".doubleblind"
BINDING_FILE = "doubleblind.binding.yaml"
FINISH_HINT = (
    "\n\nAll contract scenarios are green: call finish now. finish also runs the repository "
    "tests and checks the binding, and tells you what is still missing."
)
ALLOWED_COMMANDS = (
    "pytest",
    "python -m pytest",
    "python",
    "npm test",
    "npx vitest",
    "node",
    "git status",
    "git diff",
    "ruff",
    "eslint",
)
READ_LIMIT = 400
SEARCH_LIMIT = 100
OUTPUT_LINES = 200
APP_LOG_LINES = 30
RUN_TIMEOUT_S = 300
ENV_RETRIES = 2
SIDE_PORTS = {"consumer": 18000, "provider": 18100}
TEST_FILE = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*\.test\.[cm]?[jt]s$)")
GIT_ENV = {"HOME": "/root", "NO_COLOR": "1"}


def tool(
    name: str, description: str, properties: dict[str, Any], required: list[str]
) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {"type": "object", "properties": properties, "required": required},
        },
    }


TEXT = {"type": "string"}
TOOLS = [
    tool("list_dir", "List a directory of the repository.", {"path": TEXT}, ["path"]),
    tool(
        "read_file",
        "Read a file with line numbers, at most 400 lines per call.",
        {"path": TEXT, "start_line": {"type": "integer"}, "end_line": {"type": "integer"}},
        ["path"],
    ),
    tool(
        "search",
        "Search the repository with ripgrep.",
        {"pattern": TEXT, "glob": TEXT},
        ["pattern"],
    ),
    tool(
        "write_file",
        "Create or overwrite a file.",
        {"path": TEXT, "content": TEXT},
        ["path", "content"],
    ),
    tool(
        "edit_file",
        "Replace an exact, unique string in a file.",
        {
            "path": TEXT,
            "old_string": TEXT,
            "new_string": TEXT,
            "replace_all": {"type": "boolean"},
        },
        ["path", "old_string", "new_string"],
    ),
    tool(
        "run",
        "Run an allowed command (tests, python, node, git status/diff, linters) without network.",
        {"command": TEXT, "timeout_s": {"type": "integer"}},
        ["command"],
    ),
    tool(
        "contract_tests",
        "Run the contract tests of your side and get a triaged result.",
        {"scenarios": {"type": "array", "items": TEXT}},
        [],
    ),
    tool(
        "raise_contract_gap",
        "Stop: the contract is contradictory or impossible on your side.",
        {"scenario_ids": {"type": "array", "items": TEXT}, "description": TEXT},
        ["scenario_ids", "description"],
    ),
    tool(
        "finish",
        "Finish when all contract tests and repository tests pass.",
        {"summary": TEXT},
        ["summary"],
    ),
]


class ToolError(Exception):
    pass


@dataclass
class Outcome:
    kind: Literal["done", "contract_gap", "stuck"]
    summary: str
    report: dict[str, Any] | None = None
    scenario_ids: list[str] = field(default_factory=list)
    transcript: list[dict[str, Any]] = field(default_factory=list)


class Finished(Exception):
    def __init__(self, outcome: Outcome) -> None:
        super().__init__(outcome.kind)
        self.outcome = outcome


def inside_repo(path: str) -> str:
    full = posixpath.normpath(posixpath.join(REPO_DIR, path))
    if full != REPO_DIR and not full.startswith(f"{REPO_DIR}/"):
        raise ToolError("paths must stay inside /workspace/repo")
    return full


def indentation(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def shift(line: str, delta: int) -> str:
    if not line.strip():
        return line
    if delta >= 0:
        return " " * delta + line
    return line[min(-delta, indentation(line)) :]


def replace_ignoring_indent(text: str, old: str, new: str) -> str | None:
    lines, wanted = text.split("\n"), old.strip("\n").split("\n")
    stripped = [line.strip() for line in wanted]
    starts = [
        start
        for start in range(len(lines) - len(wanted) + 1)
        if all(lines[start + offset].strip() == stripped[offset] for offset in range(len(wanted)))
    ]
    if len(starts) != 1:
        return None
    start = starts[0]
    delta = indentation(lines[start]) - indentation(wanted[0])
    replacement = [shift(line, delta) for line in new.strip("\n").split("\n")]
    return "\n".join([*lines[:start], *replacement, *lines[start + len(wanted) :]])


def describe_failure(failure: dict[str, Any]) -> str:
    where = f"step {failure['step']}" if failure["step"] is not None else "after all steps"
    path = f" {failure['path']}" if failure["path"] else ""
    values = ""
    if failure["expected"] is not None or failure["actual"] is not None:
        values = f" (expected {failure['expected']!r}, got {failure['actual']!r})"
    return f"{where}{path}: {failure['message']}{values}"[:400]


def tail(text: str, lines: int = OUTPUT_LINES) -> str:
    return "\n".join(text.splitlines()[-lines:])


class Toolbox:
    def __init__(
        self,
        sandbox: ServiceSandbox,
        workspace: Workspace,
        llm: LLMClient,
        party: str,
        side: str,
        contract_yaml: str,
        secrets: dict[str, str],
        branch: str,
    ) -> None:
        self.sandbox = sandbox
        self.workspace = workspace
        self.llm = llm
        self.party = party
        self.side = side
        self.contract_yaml = contract_yaml
        self.secrets = secrets
        self.branch = branch
        self.base_commit = ""
        self.changed: set[str] = set()
        self.last_report: dict[str, Any] | None = None
        self.green_head: str | None = None

    async def shell(self, command: str, persist: bool = False, **options: Any) -> ExecResult:
        env = {**GIT_ENV, **options.pop("env", {})}
        return await self.sandbox.run(self.workspace, command, persist=persist, env=env, **options)

    async def prepare(self) -> None:
        await self.shell(f"git checkout -q -B {shlex.quote(self.branch)}", persist=True)
        result = await self.shell("git rev-list --max-parents=0 HEAD")
        self.base_commit = result.stdout.strip().splitlines()[0]

    async def commit(self, message: str) -> None:
        await self.shell(
            f"git add -A -- . ':!{WORK_DIR}' && git commit -q -m {shlex.quote(message)} || true",
            persist=True,
        )

    async def call(self, name: str, args: dict[str, Any]) -> str:
        handler = getattr(self, f"tool_{name}", None)
        if handler is None:
            return f"error: unknown tool {name}"
        try:
            result: str = await handler(**args)
        except ToolError as error:
            result = f"error: {error}"
        except TypeError as error:
            result = f"error: bad arguments for {name}: {error}"
        return self.scrub(result)

    def scrub(self, text: str) -> str:
        for secret, value in self.secrets.items():
            text = text.replace(value, f"<secret:{secret}>")
        return text

    async def tool_list_dir(self, path: str) -> str:
        result = await self.shell(f"ls -1Ap {shlex.quote(inside_repo(path))}")
        if not result.ok:
            raise ToolError(tail(result.output(), 5))
        entries = [
            line for line in result.stdout.splitlines() if line not in (".git/", "node_modules/")
        ]
        return "\n".join(entries) or "(empty)"

    async def read_text(self, path: str) -> str:
        try:
            data = await self.sandbox.read_file(self.workspace, inside_repo(path))
        except SandboxError as error:
            raise ToolError(f"cannot read {path}: {error}") from error
        return data.decode("utf-8", errors="replace")

    async def tool_read_file(
        self, path: str, start_line: int = 1, end_line: int | None = None
    ) -> str:
        lines = (await self.read_text(path)).splitlines()
        start = max(start_line, 1)
        end = min(end_line or len(lines), start + READ_LIMIT - 1, len(lines))
        numbered = [f"{number}\t{lines[number - 1]}" for number in range(start, end + 1)]
        more = f"\n... {len(lines) - end} more lines" if end < len(lines) else ""
        return "\n".join(numbered) + more if numbered else "(empty file)"

    async def tool_search(self, pattern: str, glob: str | None = None) -> str:
        scope = f" -g {shlex.quote(glob)}" if glob else ""
        result = await self.shell(
            f"rg -n --sort path --no-heading --max-columns 300 -e {shlex.quote(pattern)}{scope}"
            " -g '!.git' ."
            f" | head -n {SEARCH_LIMIT}"
        )
        return result.stdout.strip() or "no matches"

    async def write(self, path: str, content: str) -> None:
        await self.sandbox.write_files(self.workspace, {inside_repo(path): content.encode("utf-8")})
        self.changed.add(path)

    async def tool_write_file(self, path: str, content: str | None = None) -> str:
        if posixpath.basename(path) == "contract.yaml":
            raise ToolError(
                "contract_tests supplies the signed contract itself; never write it to the "
                "repository. Change the code and the binding, then run contract_tests."
            )
        if content is None:
            raise ToolError("write_file needs the full content of the file")
        await self.write(path, content)
        return f"wrote {path} ({len(content.splitlines())} lines)"

    async def tool_edit_file(
        self, path: str, old_string: str, new_string: str, replace_all: bool = False
    ) -> str:
        text = await self.read_text(path)
        count = text.count(old_string)
        if count == 0:
            adjusted = replace_ignoring_indent(text, old_string, new_string)
            if adjusted is None:
                raise ToolError(
                    "old_string was not found; copy it exactly from read_file (the text after "
                    "the tab), including indentation"
                )
            await self.write(path, adjusted)
            return f"edited {path} (1 replacement, indentation adjusted)"
        if count > 1 and not replace_all:
            raise ToolError(f"old_string occurs {count} times; make it unique or set replace_all")
        await self.write(path, text.replace(old_string, new_string))
        return f"edited {path} ({count if replace_all else 1} replacement)"

    async def tool_run(self, command: str, timeout_s: int = RUN_TIMEOUT_S) -> str:
        if not command.strip().startswith(ALLOWED_COMMANDS):
            raise ToolError(
                f"only these commands are allowed: {', '.join(ALLOWED_COMMANDS)}; they already "
                "run inside /workspace/repo, so do not use cd"
            )
        result = await self.shell(command, timeout_s=min(timeout_s, RUN_TIMEOUT_S))
        state = "timed out" if result.timed_out else f"exit {result.exit_code}"
        return f"{state}\n{tail(result.output())}"

    def side_env(self) -> dict[str, str]:
        prefix, port = self.side.upper(), SIDE_PORTS[self.side]
        env = {f"{prefix}_PORT": str(port), f"{prefix}_BASE_URL": f"http://127.0.0.1:{port}"}
        env.update({secret_env_name(name): value for name, value in self.secrets.items()})
        return env

    async def run_contract_tests(self, scenarios: list[str] | None = None) -> dict[str, Any]:
        selection = f" --scenarios {shlex.quote(','.join(scenarios))}" if scenarios else ""
        result = await self.shell(
            f"{self.sandbox.python} {WORK_DIR}/entry.py {self.side} {WORK_DIR}/contract.yaml"
            f" {BINDING_FILE} {WORK_DIR}/report.json{selection}",
            files={
                repo_path(f"{WORK_DIR}/entry.py"): ENTRY_SOURCE,
                repo_path(f"{WORK_DIR}/contract.yaml"): self.contract_yaml.encode("utf-8"),
            },
            env=self.side_env(),
            timeout_s=RUN_TIMEOUT_S,
        )
        lines = result.stdout.strip().splitlines()
        outcome: dict[str, Any] = json.loads(lines[-1]) if result.ok and lines else {}
        if outcome.get("report") is None:
            details = outcome.get("output") or result.output()
            raise ToolError(f"the contract tests could not run:\n{tail(details, 40)}")
        report: dict[str, Any] = outcome["report"]
        return report

    async def tool_contract_tests(self, scenarios: list[str] | None = None) -> str:
        for _ in range(ENV_RETRIES + 1):
            report = await self.run_contract_tests(scenarios)
            if not scenarios:
                self.last_report = report
            statuses = "\n".join(
                f"{item['id']} {item['status']}"
                + (f": {describe_failure(item['failures'][0])}" if item["failures"] else "")
                for item in report["scenarios"]
            )
            if not failing(report):
                await self.commit("doubleblind: contract tests green")
                if not scenarios:
                    self.green_head = self.workspace.head
                hint = FINISH_HINT if not scenarios else ""
                return f"all {len(report['scenarios'])} scenarios passed\n{statuses}{hint}"
            verdict = await triage(self.llm, json.loads(self.scrub(json.dumps(report))), self.party)
            if verdict.category != "ENV_ERROR":
                break
        first_failure = failing(report)[0]
        log = tail(first_failure.get("app_log_tail", ""), APP_LOG_LINES)
        return (
            f"{statuses}\n\ntriage: {verdict.category}, next step {verdict.suggested_next_step}\n"
            f"{verdict.summary_for_coder}\n\nYour app log during {first_failure['id']}:\n{log}"
        )

    async def tool_raise_contract_gap(self, scenario_ids: list[str], description: str) -> str:
        raise Finished(Outcome("contract_gap", description, scenario_ids=scenario_ids))

    async def repo_tests(self) -> ExecResult:
        has_package = await self.shell("test -f package.json")
        command = "npm test" if has_package.ok else "python -m pytest -q -p no:cacheprovider"
        return await self.shell(command, timeout_s=RUN_TIMEOUT_S)

    async def weakened_tests(self) -> list[str]:
        result = await self.shell(f"git add -A -N . && git diff --numstat {self.base_commit} -- .")
        rows = [line.split("\t") for line in result.stdout.splitlines()]
        return [
            row[2]
            for row in rows
            if len(row) == 3 and TEST_FILE.search(row[2]) and row[1].isdigit() and row[1] != "0"
        ]

    async def binding_problems(self) -> list[str]:
        binding, report = check_binding_text(await self.read_text(BINDING_FILE))
        if binding is None:
            return [finding.render() for finding in report.errors]
        contract = parse_yaml(self.contract_yaml)
        return [
            finding.message
            for finding in validate_contract_data(contract, [binding]).errors
            if finding.path.endswith((".action", ".query"))
        ]

    async def tool_finish(self, summary: str) -> str:
        report = await self.run_contract_tests()
        self.last_report = report
        red = [item["id"] for item in failing(report)]
        if red:
            return f"finish refused: contract scenarios still failing: {', '.join(red)}"
        missing = await self.binding_problems()
        if missing:
            return (
                "finish refused: the e2e scenarios run against the other company later and need "
                "these in your binding (with working test hooks):\n" + "\n".join(missing)
            )
        repo = await self.repo_tests()
        if not repo.ok:
            return f"finish refused: repository tests fail:\n{tail(repo.output(), 60)}"
        weakened = await self.weakened_tests()
        if weakened:
            return (
                "finish refused: existing tests lost lines (only additions are allowed): "
                + ", ".join(weakened)
            )
        await self.commit("doubleblind: finish")
        raise Finished(Outcome("done", summary, report))
