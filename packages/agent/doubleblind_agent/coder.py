import json
from collections.abc import Awaitable, Callable
from importlib.resources import files
from typing import Any

from doubleblind_agent.models import Psm
from doubleblind_agent.prompts import BINDING_GUIDE, CODER, CONTRACT_GUIDE
from doubleblind_agent.stable import stable
from doubleblind_agent.tools import TOOLS, Finished, Outcome, Toolbox
from doubleblind_contract.loader import parse_yaml
from doubleblind_llm import LLMClient, LLMRequest, Role

MAX_STEPS = 80
RECENT_RESULTS = 10
TRIMMED_RESULT = 300
REPEAT_LIMIT = 3
REPEATED = (
    "You made this exact call several times in a row. Use what you already have and make "
    "progress: edit or write files, or run contract_tests."
)
TEST_HOOKS_ENV = {"consumer": "DOUBLEBLIND_TEST_HOOKS=1", "provider": "SANDBOX=1"}
EXAMPLES = "\n\n".join(
    files("doubleblind_agent").joinpath(name).read_text("utf-8")
    for name in ("example_binding_consumer.yaml", "example_binding_provider.yaml")
)

Progress = Callable[[int, int, str], Awaitable[None]]


def briefing(
    contract_yaml: str, psm: Psm, party: str, side: str, e2e_feedback: Any, previous: str
) -> str:
    contract = parse_yaml(contract_yaml)
    scenarios = [
        f"{item['id']}: {item['title']}" for item in contract["scenarios"] if item["side"] == side
    ]
    decisions = [
        f"{item['id']}: {item['text']}"
        for item in contract.get("decisions", [])
        if item["owner"] == side
    ]
    end_to_end = [
        f"{item['id']}: {item['title']}" for item in contract["scenarios"] if item["side"] == "e2e"
    ]
    parts = [
        f"Signed contract:\n{contract_yaml}",
        "Scenarios of your side:\n" + "\n".join(scenarios),
        "End-to-end scenarios, run later against the other company's real app; your binding must "
        f"provide every action and query they use for party {side}:\n" + "\n".join(end_to_end),
        "Decisions owned by your side:\n" + ("\n".join(decisions) or "none"),
        "Where to integrate (private system model):\n"
        + json.dumps([point.model_dump() for point in psm.integration_points], ensure_ascii=False),
        f"How to run the app and tests:\n{psm.test_setup.model_dump_json()}",
    ]
    if previous:
        parts.append(
            "You already worked on this task in an earlier session; your changes are in the "
            f"repository (git diff shows them). Where that session stopped:\n{previous}"
        )
    if e2e_feedback:
        parts.append(
            "The end-to-end test with the other company failed. Sanitized report:\n"
            + stable(json.dumps(e2e_feedback, ensure_ascii=False))
        )
    return "\n\n".join(parts)


def trimmed(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    results = [index for index, message in enumerate(messages) if message["role"] == "tool"]
    old = set(results[:-RECENT_RESULTS])
    return [
        {**message, "content": message["content"][:TRIMMED_RESULT] + " [older output trimmed]"}
        if index in old and len(message["content"]) > TRIMMED_RESULT
        else message
        for index, message in enumerate(messages)
    ]


def note(tool_name: str, toolbox: Toolbox) -> str:
    report = toolbox.last_report
    if tool_name != "contract_tests" or report is None:
        return tool_name
    passed = sum(1 for item in report["scenarios"] if item["status"] == "passed")
    return f"contract tests: {passed}/{len(report['scenarios'])} green"


def assistant_message(content: str | None, calls: list[Any]) -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": content or "",
        "tool_calls": [
            {
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
            }
            for call in calls
        ],
    }


async def run_coder(
    llm: LLMClient,
    toolbox: Toolbox,
    psm: Psm,
    contract_yaml: str,
    e2e_feedback: Any,
    previous: str,
    progress: Progress,
) -> Outcome:
    party, side = toolbox.party, toolbox.side
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": CODER.format(
                party=party, role=side, side=side, test_hooks_env=TEST_HOOKS_ENV[side]
            ),
        },
        {"role": "system", "content": f"{CONTRACT_GUIDE}\n\n{BINDING_GUIDE}\n\n{EXAMPLES}"},
        {
            "role": "user",
            "content": briefing(contract_yaml, psm, party, side, e2e_feedback, previous),
        },
    ]
    recent: list[str] = []
    for step in range(1, MAX_STEPS + 1):
        response = await llm.complete(
            LLMRequest(role=Role.CODER, messages=trimmed(messages), tools=TOOLS, party=party)
        )
        if not response.tool_calls:
            messages += [
                {"role": "assistant", "content": response.content or ""},
                {"role": "user", "content": "Continue with a tool call; call finish when done."},
            ]
            continue
        messages.append(assistant_message(response.content, response.tool_calls))
        for call in response.tool_calls:
            signature = json.dumps([call.name, call.arguments], sort_keys=True)
            recent.append(signature)
            if recent[-REPEAT_LIMIT:] == [signature] * REPEAT_LIMIT:
                messages.append({"role": "tool", "tool_call_id": call.id, "content": REPEATED})
                continue
            try:
                result = await toolbox.call(call.name, call.arguments)
            except Finished as finished:
                await progress(step, len(toolbox.changed), f"{call.name}: {finished.outcome.kind}")
                finished.outcome.transcript = messages
                return finished.outcome
            messages.append({"role": "tool", "tool_call_id": call.id, "content": stable(result)})
            await progress(step, len(toolbox.changed), note(call.name, toolbox))
    if toolbox.green_head is not None:
        if toolbox.workspace.head != toolbox.green_head:
            toolbox.workspace.advance(toolbox.green_head)
        try:
            refused = await toolbox.tool_finish(
                "Step limit reached; finished from the last fully green run."
            )
        except Finished as finished:
            finished.outcome.transcript = messages
            return finished.outcome
        return Outcome("stuck", stable(refused), toolbox.last_report, transcript=messages)
    report = toolbox.last_report
    return Outcome("stuck", f"no finish after {MAX_STEPS} steps", report, transcript=messages)
