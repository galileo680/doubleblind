import json
from typing import Any

import jsonpatch

from doubleblind_agent.coder import EXAMPLES, TEST_HOOKS_ENV, assistant_message, trimmed
from doubleblind_agent.models import Psm
from doubleblind_agent.negotiator import patched, split_patch
from doubleblind_agent.prompts import BINDING_GUIDE, CONTRACT_GUIDE, DRY_RUN
from doubleblind_agent.stable import stable
from doubleblind_agent.tools import (
    BINDING_FILE,
    TEXT,
    TOOLS,
    Toolbox,
    ToolError,
    inside_repo,
    tool,
)
from doubleblind_contract.validate import PROVIDER_SECTIONS, validate_contract_data
from doubleblind_llm import LLMClient, LLMRequest, Role

MAX_STEPS = 30
PATCH = {"type": "object"}
DRY_RUN_TOOLS = [
    item
    for item in TOOLS
    if item["function"]["name"]
    in ("list_dir", "read_file", "search", "write_file", "contract_tests")
] + [
    tool(
        "patch_contract",
        "Correct your part of the draft contract with JSON Patch operations {op, path, value}.",
        {"patches": {"type": "array", "items": PATCH}},
        ["patches"],
    ),
    tool(
        "report_mismatches",
        "Finish the dry run: one line per mismatch between the contract and the real system.",
        {"mismatches": {"type": "array", "items": TEXT}},
        ["mismatches"],
    ),
]


class Reported(Exception):
    def __init__(self, mismatches: list[str]) -> None:
        super().__init__("reported")
        self.mismatches = mismatches


class DryRunToolbox(Toolbox):
    def __init__(self, *args: Any, locked: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.locked = locked
        self.fixes: list[dict[str, Any]] = []

    async def tool_write_file(self, path: str, content: str | None = None) -> str:
        if inside_repo(path) != inside_repo(BINDING_FILE):
            raise ToolError(
                f"a dry run may only write {BINDING_FILE}; never change the application"
            )
        return await super().tool_write_file(path, content)

    async def tool_report_mismatches(self, mismatches: list[str]) -> str:
        raise Reported(mismatches)

    async def tool_patch_contract(self, patches: Any) -> str:
        patches = [loaded(item) for item in loaded(patches)]
        if not all(isinstance(item, dict) and "op" in item and "path" in item for item in patches):
            raise ToolError("patches must be a list of objects {op, path, value}")
        patches = [part for item in patches for part in split_patch(item)]
        contract = json.loads(self.contract_yaml)
        for patch in patches:
            problem = ownership(contract, patch, self.side, self.locked)
            if problem:
                raise ToolError(problem)
        try:
            updated = patched(contract, patches)
        except (jsonpatch.JsonPatchException, jsonpatch.JsonPointerException, KeyError) as error:
            raise ToolError(f"the patch does not apply: {error}") from error
        errors = [finding.render() for finding in validate_contract_data(updated).errors]
        if errors:
            raise ToolError("the contract would be invalid:\n" + "\n".join(errors[:15]))
        self.contract_yaml = contract_text(updated)
        self.fixes += patches
        return f"contract updated with {len(patches)} patches; run contract_tests again"


def loaded(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    try:
        return json.loads(value)
    except ValueError as error:
        raise ToolError(f"not valid JSON: {error}") from error


def ownership(
    contract: dict[str, Any], patch: dict[str, Any], side: str, locked: bool
) -> str | None:
    parts = str(patch.get("path", "")).strip("/").split("/")
    if parts[:2] == ["setup", side]:
        return None
    if parts[0] in PROVIDER_SECTIONS:
        return (
            f"/{parts[0]} comes from your OpenAPI specification and is fixed; correct the "
            f"{side} scenarios instead"
            if locked
            else None
        )
    if parts[0] != "scenarios" or len(parts) < 2:
        return f"only your API sections, /setup/{side} and {side} or e2e scenarios may change"
    if parts[1] == "-":
        value = patch.get("value") or {}
        return None if value.get("side") == side else f"new scenarios must have side {side}"
    if len(parts) == 2 and patch.get("op") == "remove":
        return "never remove a scenario; correct its wrong values instead"
    scenarios = contract.get("scenarios") or []
    index = int(parts[1]) if parts[1].isdigit() else -1
    if not 0 <= index < len(scenarios) or scenarios[index].get("side") not in (side, "e2e"):
        return f"only {side} and e2e scenarios may change"
    return None


def contract_text(contract: dict[str, Any]) -> str:
    return json.dumps(contract, ensure_ascii=False, indent=2)


def removals(contract: dict[str, Any], report: dict[str, Any], side: str) -> list[dict[str, Any]]:
    results = {item["id"]: item["status"] for item in report["scenarios"]}
    if "passed" not in results.values():
        return []
    doomed = [
        index
        for index, item in enumerate(contract["scenarios"])
        if item.get("side") == side and results.get(item["id"]) != "passed"
    ]
    return [{"op": "remove", "path": f"/scenarios/{index}"} for index in reversed(doomed)]


async def dry_run(
    llm: LLMClient, toolbox: DryRunToolbox, psm: Psm, binding: str | None
) -> tuple[list[dict[str, Any]], str | None, int]:
    party, side = toolbox.party, toolbox.side
    contract = json.loads(toolbox.contract_yaml)
    scenarios = [
        f"{item['id']}: {item['title']}" for item in contract["scenarios"] if item["side"] == side
    ]
    parts = [
        f"Draft contract (JSON; patch paths follow it):\n{toolbox.contract_yaml}",
        "Scenarios of your side:\n" + "\n".join(scenarios),
        "Your APIs and test endpoints (private system model):\n"
        + json.dumps([api.model_dump() for api in psm.apis_exposed], ensure_ascii=False),
        f"How to run the app and tests:\n{psm.test_setup.model_dump_json()}",
    ]
    if binding:
        await toolbox.write(BINDING_FILE, binding)
        parts.append(
            "Your binding from the previous check is already in the repository; run "
            "contract_tests first."
        )
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": DRY_RUN.format(party=party, side=side, test_hooks_env=TEST_HOOKS_ENV[side]),
        },
        {"role": "system", "content": f"{CONTRACT_GUIDE}\n\n{BINDING_GUIDE}\n\n{EXAMPLES}"},
        {"role": "user", "content": "\n\n".join(parts)},
    ]
    finished = False
    for _ in range(MAX_STEPS):
        if finished:
            break
        response = await llm.complete(
            LLMRequest(
                role=Role.CODER, messages=trimmed(messages), tools=DRY_RUN_TOOLS, party=party
            )
        )
        if not response.tool_calls:
            messages += [
                {"role": "assistant", "content": response.content or ""},
                {
                    "role": "user",
                    "content": "Continue with a tool call; end with report_mismatches.",
                },
            ]
            continue
        messages.append(assistant_message(response.content, response.tool_calls))
        for call in response.tool_calls:
            try:
                result = await toolbox.call(call.name, call.arguments)
            except Reported:
                finished = True
                break
            messages.append({"role": "tool", "tool_call_id": call.id, "content": stable(result)})
    try:
        report = await toolbox.run_contract_tests()
    except ToolError:
        report = {"scenarios": []}
    removed = removals(json.loads(toolbox.contract_yaml), report, side)
    try:
        written: str | None = await toolbox.read_text(BINDING_FILE)
    except ToolError:
        written = binding
    return [*toolbox.fixes, *removed], written, len(removed)
