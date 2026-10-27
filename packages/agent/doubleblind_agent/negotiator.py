import copy
import json
from collections.abc import Callable
from importlib.resources import files
from typing import Any

import jsonpatch
from pydantic import ValidationError

from doubleblind_agent.broker_client import BrokerClient
from doubleblind_agent.models import Patch, Psm, TavilyTurn, Turn
from doubleblind_agent.prompts import AMEND, CONTRACT_GUIDE, NEGOTIATOR, PHASES, TAVILY_RULE
from doubleblind_agent.psm import SCOPE
from doubleblind_agent.standards import enabled
from doubleblind_contract.loader import parse_yaml
from doubleblind_contract.validate import (
    foreign_patch,
    provider_scenarios,
    signing_problems,
    validate_contract_data,
)
from doubleblind_llm import LLMClient, LLMRequest, Role
from doubleblind_llm.errors import StructuredOutputError

RECENT_TURNS = 10
EXAMPLE_CONTRACT = files("doubleblind_agent").joinpath("example_contract.yaml").read_text("utf-8")
INTERNAL_FIXES = 4
SHOWN_PROBLEMS = 25
STRUCTURED_ATTEMPTS = 3
MAX_QUESTIONS = 2


PATCH_ERRORS = (jsonpatch.JsonPatchException, jsonpatch.JsonPointerException)


STEP_KINDS = {
    "do",
    "send",
    "emit",
    "expect_received",
    "expect_no_request",
    "expect_emitted",
    "expect_state",
}


def merged(item: Any) -> bool:
    return isinstance(item, dict) and len(item) > 1 and set(item) <= STEP_KINDS


def steps_of(item: dict[str, Any]) -> list[dict[str, Any]]:
    return [{kind: split_steps(body)} for kind, body in item.items() if body]


def split_steps(value: Any) -> Any:
    if isinstance(value, list):
        return [
            part
            for item in value
            for part in (steps_of(item) if merged(item) else [split_steps(item)])
        ]
    if isinstance(value, dict):
        return {key: split_steps(item) for key, item in value.items()}
    return value


def split_patch(patch: dict[str, Any]) -> list[dict[str, Any]]:
    head, _, last = patch["path"].rpartition("/")
    if patch["op"] == "add" and merged(patch.get("value")) and (last == "-" or last.isdigit()):
        parts = steps_of(patch["value"])
        return [
            {
                **patch,
                "path": f"{head}/{int(last) + index}" if last.isdigit() else patch["path"],
                "value": part,
            }
            for index, part in enumerate(parts)
        ]
    if "value" in patch:
        return [{**patch, "value": split_steps(patch["value"])}]
    return [patch]


def patched(document: dict[str, Any], patches: list[dict[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = jsonpatch.apply_patch(document, copy.deepcopy(patches))
    return result


def split_patches(
    contract: dict[str, Any], patches: list[dict[str, Any]]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    document, kept, skipped = contract, [], []
    for patch in patches:
        variants = [patch, {**patch, "op": "add"}] if patch["op"] == "replace" else [patch]
        for variant in variants if patch["path"].strip("/") else []:
            try:
                document = patched(document, [variant])
            except PATCH_ERRORS:
                continue
            kept.append(variant)
            break
        else:
            skipped.append(patch)
    return kept, skipped


def applicable_patches(
    contract: dict[str, Any], patches: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    return split_patches(contract, patches)[0]


def skipped_problems(contract: dict[str, Any], turn: Turn) -> list[str]:
    return [
        f"patch '{patch['op']} {patch['path']}' does not apply to the current contract (that path "
        "does not exist there); rewrite it with the exact paths of the current contract"
        for patch in split_patches(contract, turn.patch_list())[1]
    ]


def cited(
    contract: dict[str, Any], turn: TavilyTurn, standards: list[dict[str, Any]]
) -> TavilyTurn:
    known = {url for item in standards for url in item["sources"]}
    document = patched(contract, applicable_patches(contract, turn.patch_list()))
    decisions = [item if isinstance(item, dict) else {} for item in document.get("decisions") or []]
    extra = []
    for citation in turn.citations:
        index = next(
            (i for i, item in enumerate(decisions) if item.get("id") == citation.decision_id), None
        )
        if index is None or citation.source_url not in known:
            continue
        text = str(decisions[index].get("text", ""))
        if citation.source_url in text:
            continue
        decisions[index] = {**decisions[index], "text": f"{text} Source: {citation.source_url}"}
        extra.append(
            Patch(op="replace", path=f"/decisions/{index}/text", value=decisions[index]["text"])
        )
    return turn.model_copy(update={"patches": [*turn.patches, *extra]})


def valid_subset(contract: dict[str, Any], turn: Turn) -> Turn:
    kept: list[Patch] = []
    errors = len(patch_errors(contract, turn.model_copy(update={"patches": []})))
    for item in turn.patches:
        count = len(patch_errors(contract, turn.model_copy(update={"patches": [*kept, item]})))
        if count <= errors:
            kept, errors = [*kept, item], count
    return turn.model_copy(update={"patches": kept if errors == 0 else []})


def patch_errors(contract: dict[str, Any], turn: Turn) -> list[str]:
    candidate = patched(contract, applicable_patches(contract, turn.patch_list()))
    candidate = {**candidate, "version": contract["version"] + 1, "status": "proposed"}
    problems = [finding.render() for finding in validate_contract_data(candidate).errors]
    if turn.status == "ready_to_sign":
        problems += signing_problems(candidate)
    return problems


async def negotiation_context(broker: BrokerClient) -> dict[str, Any]:
    session = await broker.get("")
    contract_yaml = await broker.get("/contract")
    return {
        "session": session,
        "contract_yaml": contract_yaml,
        "contract": parse_yaml(contract_yaml),
        "counterparty": await broker.get("/briefs/counterparty"),
        "turns": [turn for turn in await broker.get("/turns") if turn["accepted"]],
        "questions": await broker.get("/questions"),
    }


def history(turns: list[dict[str, Any]]) -> str:
    lines = []
    for turn in turns[-RECENT_TURNS:]:
        changed = ", ".join(f"{patch['op']} {patch['path']}" for patch in turn["patches"]) or "none"
        lines.append(
            f"Round {turn['round']} by {turn['party']} (status {turn['status']}, "
            f"version after {turn['contract_version_after']}): {turn['message']}\n"
            f"Changed: {changed}"
        )
    return "\n\n".join(lines) or "No turns yet."


def standards(context: dict[str, Any]) -> str:
    return "\n".join(
        f"- {item['query']}: {item['answer']} (sources: {', '.join(item['sources'])})"
        if item["sources"]
        else f"- {item['query']}: no result"
        for item in context.get("standards", [])
    )


def messages(
    psm: Psm,
    party: str,
    role: str,
    context: dict[str, Any],
    synthetic: dict[str, list[str]],
    feedback: list[str],
) -> list[dict[str, Any]]:
    questions = [
        f"{item['id']}: {item['text']} -> {item['answer'] or 'not answered yet'}"
        for item in context["questions"]
    ]
    counterparty = json.dumps(context["counterparty"], ensure_ascii=False, indent=2)
    parts = [
        f"Your private system model:\n{psm.model_dump_json(indent=2)}",
        f"The other company's brief:\n{counterparty}",
        f"Current contract (version {context['session']['current_contract_version']}):\n"
        f"{context['contract_yaml']}",
        f"Recent turns:\n{history(context['turns'])}",
        "Your questions to your human and their answers:\n" + ("\n".join(questions) or "none"),
        f"This is round {context['session']['round'] + 1} of at most "
        f"{context['session']['max_rounds']}.",
        "Phone numbers and e-mail addresses in examples may only be these synthetic values: "
        + ", ".join([*synthetic["phones"], *synthetic["emails"]]),
    ]
    if context.get("scope"):
        parts.insert(
            0,
            f"{SCOPE}{context['scope']}\nThe contract implements exactly this scope: no "
            "operations, webhooks, decisions or scenarios for anything outside it. If your "
            "system does not support part of this scope, ask your human about it in your first "
            "turn instead of leaving it out silently.",
        )
    if context.get("api_from_spec"):
        parts.insert(
            2,
            "Your API (operations, schemas, webhooks, secrets) comes from your OpenAPI "
            "specification and is already in the contract. It is fixed: never patch these "
            "sections; negotiate decisions and scenarios that use them.",
        )
    if enabled():
        parts.insert(
            5,
            "Public standards you looked up with web search:\n"
            + (standards(context) or "none yet"),
        )
    if context.get("gap"):
        gap = context["gap"]
        named = [
            item
            for item in context["contract"].get("scenarios") or []
            if item.get("id") in gap["scenario_ids"]
        ]
        parts.append(
            f"The signed contract could not be implemented: {gap['party']} reported a contract gap "
            f"in scenarios {', '.join(gap['scenario_ids']) or 'not given'}: {gap['description']}\n"
            + "".join(
                f"Current steps of {item['id']}: {json.dumps(item.get('steps'))}\n"
                for item in named
            )
            + "Fix the contract so both sides can implement and test it, then set ready_to_sign."
        )
    first = not any(turn["party"] == party for turn in context["turns"])
    parts.append(AMEND if context.get("gap") else PHASES[(role, first)])
    if feedback:
        parts.append(
            "Your previous attempt was rejected; fix these problems:\n"
            + "\n".join(f"- {item}" for item in feedback)
        )
    return [
        {
            "role": "system",
            "content": NEGOTIATOR.format(party=party, role=role)
            + (f"\n{TAVILY_RULE}" if enabled() else ""),
        },
        {
            "role": "system",
            "content": f"{CONTRACT_GUIDE}\n\n"
            "A valid contract of a different integration, to show the shape of every section "
            f"(do not copy its content):\n{EXAMPLE_CONTRACT}",
        },
        {"role": "user", "content": "\n\n".join(parts)},
    ]


def turn_payload(context: dict[str, Any], turn: Turn) -> dict[str, Any]:
    return {
        "round": context["session"]["round"] + 1,
        "based_on_version": context["session"]["current_contract_version"],
        "message": turn.message,
        "patches": applicable_patches(context["contract"], turn.patch_list()),
        "questions_for_human": [question.model_dump() for question in turn.questions_for_human],
        "status": turn.status,
    }


async def propose_turn(
    llm: LLMClient,
    psm: Psm,
    party: str,
    role: str,
    context: dict[str, Any],
    synthetic: dict[str, list[str]],
    vet: Callable[[dict[str, Any]], list[str]],
    feedback: list[str],
) -> dict[str, Any]:
    model = TavilyTurn if enabled() else Turn

    def parsed(answer: Any) -> Turn:
        turn = model.model_validate(answer)
        parts = [part for item in turn.patches for part in split_patch(item.model_dump())]
        return turn.model_copy(update={"patches": [Patch.model_validate(part) for part in parts]})

    async def ask(conversation: list[dict[str, Any]], role_name: Role = Role.NEGOTIATOR) -> Turn:
        request = LLMRequest(
            role=role_name,
            messages=conversation,
            response_schema=model.model_json_schema(),
            party=party,
        )
        for _ in range(STRUCTURED_ATTEMPTS - 1):
            try:
                return parsed((await llm.complete(request)).parsed)
            except (StructuredOutputError, ValidationError):
                continue
        return parsed((await llm.complete(request)).parsed)

    locked = bool(context.get("api_from_spec"))
    provider_owned = provider_scenarios(context["contract"])
    gap = context.get("gap") or {}
    amended = {
        str(index)
        for index, item in enumerate(context["contract"].get("scenarios") or [])
        if item.get("id") in gap.get("scenario_ids", [])
    }

    def reason_of(path: str) -> str | None:
        found = foreign_patch(path, role, locked, provider_owned)
        parts = path.strip("/").split("/")
        if found or not amended or parts[:2] == ["setup", role]:
            return found
        if parts[0] == "scenarios" and parts[1:2] and parts[1] in amended:
            return None
        return (
            f"while amending only the scenarios named in the gap "
            f"({', '.join(gap['scenario_ids'])}) and /setup/{role} may change"
        )

    def foreign(candidate: Turn) -> list[str]:
        paths = [item.path for item in candidate.patches]
        return sorted({reason for path in paths if (reason := reason_of(path))})

    def own(candidate: Turn) -> Turn:
        kept = [patch for patch in candidate.patches if not reason_of(patch.path)]
        return candidate.model_copy(update={"patches": kept})

    def problems_of(candidate: Turn) -> list[str]:
        return (
            skipped_problems(context["contract"], candidate)
            + patch_errors(context["contract"], candidate)
            + vet(turn_payload(context, candidate))
        )

    conversation = messages(psm, party, role, context, synthetic, feedback)
    first = await ask(conversation)
    pending = foreign(first)
    dropped = set(pending)
    turn = own(first)
    for _ in range(INTERNAL_FIXES):
        problems = [f"a patch was not applied: {reason}" for reason in pending]
        problems += problems_of(turn)
        if not problems:
            break
        conversation = [
            *conversation,
            {"role": "assistant", "content": turn.model_dump_json()},
            {
                "role": "user",
                "content": "Your turn cannot be sent yet:\n"
                + "\n".join(f"- {problem}" for problem in problems[:SHOWN_PROBLEMS])
                + "\nReturn a turn whose patches only fix these problems. They are applied after "
                "the patches you already wrote; message, questions and status stay as before.",
            },
        ]
        try:
            fix = await ask(conversation, Role.NEGOTIATOR_FIX)
        except (StructuredOutputError, ValidationError):
            break
        pending = foreign(fix)
        dropped.update(pending)
        fix = own(fix)
        appended = turn.model_copy(update={"patches": [*turn.patches, *fix.patches]})
        replaced = turn.model_copy(update={"patches": fix.patches})
        turn = min(appended, replaced, turn, key=lambda candidate: len(problems_of(candidate)))
    if dropped:
        turn = turn.model_copy(
            update={"message": f"{turn.message}\n(Not applied: {'; '.join(sorted(dropped))}.)"}
        )
    if patch_errors(context["contract"], turn):
        turn = valid_subset(
            context["contract"], turn.model_copy(update={"status": "continue"})
        ).model_copy(
            update={
                "status": "continue",
                "message": f"{turn.message}\n(Some of my contract changes in this turn did not "
                "validate and were dropped; I will propose them again.)",
            }
        )
    elif isinstance(turn, TavilyTurn):
        turn = cited(context["contract"], turn, context.get("standards", []))
    turn = turn.model_copy(update={"questions_for_human": turn.questions_for_human[:MAX_QUESTIONS]})
    changed = applicable_patches(context["contract"], turn.patch_list())
    if patched(context["contract"], changed) == context["contract"]:
        turn = turn.model_copy(update={"patches": []})
    if (
        not turn.patches
        and not turn.questions_for_human
        and any(item["party"] == party for item in context["turns"])
        and not signing_problems(context["contract"])
    ):
        turn = turn.model_copy(update={"status": "ready_to_sign"})
    lookups = turn.lookup_standards if isinstance(turn, TavilyTurn) else []
    return {**turn_payload(context, turn), "lookup_standards": lookups}
