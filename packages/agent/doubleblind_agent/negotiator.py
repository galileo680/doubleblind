import json
from collections.abc import Callable
from importlib.resources import files
from typing import Any

import jsonpatch
from pydantic import ValidationError

from doubleblind_agent.broker_client import BrokerClient
from doubleblind_agent.models import Patch, Psm, TavilyTurn, Turn
from doubleblind_agent.prompts import CONTRACT_GUIDE, NEGOTIATOR, TAVILY_RULE
from doubleblind_agent.standards import enabled
from doubleblind_contract.loader import parse_yaml
from doubleblind_contract.validate import signing_problems, validate_contract_data
from doubleblind_llm import LLMClient, LLMRequest, Role
from doubleblind_llm.errors import StructuredOutputError

RECENT_TURNS = 10
EXAMPLE_CONTRACT = files("doubleblind_agent").joinpath("example_contract.yaml").read_text("utf-8")
INTERNAL_FIXES = 4
SHOWN_PROBLEMS = 25
STRUCTURED_ATTEMPTS = 3


PATCH_ERRORS = (jsonpatch.JsonPatchException, jsonpatch.JsonPointerException)


def applicable_patches(
    contract: dict[str, Any], patches: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    document, kept = contract, []
    for patch in patches:
        if not patch["path"].strip("/"):
            continue
        variants = [patch, {**patch, "op": "add"}] if patch["op"] == "replace" else [patch]
        for variant in variants:
            try:
                document = jsonpatch.apply_patch(document, [variant])
            except PATCH_ERRORS:
                continue
            kept.append(variant)
            break
    return kept


def cited(
    contract: dict[str, Any], turn: TavilyTurn, standards: list[dict[str, Any]]
) -> TavilyTurn:
    known = {url for item in standards for url in item["sources"]}
    document = jsonpatch.apply_patch(contract, applicable_patches(contract, turn.patch_list()))
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


def patch_errors(contract: dict[str, Any], turn: Turn) -> list[str]:
    candidate = jsonpatch.apply_patch(contract, applicable_patches(contract, turn.patch_list()))
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
    if enabled():
        parts.insert(
            5,
            "Public standards you looked up with web search:\n"
            + (standards(context) or "none yet"),
        )
    if context.get("gap"):
        gap = context["gap"]
        parts.append(
            f"The signed contract could not be implemented: {gap['party']} reported a contract gap "
            f"in scenarios {', '.join(gap['scenario_ids']) or 'not given'}: {gap['description']}\n"
            "Fix the contract so both sides can implement and test it, then set ready_to_sign."
        )
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

    async def ask(conversation: list[dict[str, Any]], role_name: Role = Role.NEGOTIATOR) -> Turn:
        request = LLMRequest(
            role=role_name,
            messages=conversation,
            response_schema=model.model_json_schema(),
            party=party,
        )
        for _ in range(STRUCTURED_ATTEMPTS - 1):
            try:
                return model.model_validate((await llm.complete(request)).parsed)
            except (StructuredOutputError, ValidationError):
                continue
        return model.model_validate((await llm.complete(request)).parsed)

    def problems_of(candidate: Turn) -> list[str]:
        return patch_errors(context["contract"], candidate) + vet(turn_payload(context, candidate))

    conversation = messages(psm, party, role, context, synthetic, feedback)
    turn = await ask(conversation)
    for _ in range(INTERNAL_FIXES):
        problems = problems_of(turn)
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
        appended = turn.model_copy(update={"patches": [*turn.patches, *fix.patches]})
        replaced = turn.model_copy(update={"patches": fix.patches})
        turn = min(appended, replaced, key=lambda candidate: len(problems_of(candidate)))
    if patch_errors(context["contract"], turn):
        turn = turn.model_copy(
            update={
                "patches": [],
                "status": "continue",
                "message": f"{turn.message}\n(My contract changes in this turn did not validate "
                "and were dropped; I will propose them again.)",
            }
        )
    elif isinstance(turn, TavilyTurn):
        turn = cited(context["contract"], turn, context.get("standards", []))
    lookups = turn.lookup_standards if isinstance(turn, TavilyTurn) else []
    return {**turn_payload(context, turn), "lookup_standards": lookups}
