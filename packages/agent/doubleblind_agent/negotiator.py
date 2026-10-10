import json
from collections.abc import Callable
from importlib.resources import files
from typing import Any

import jsonpatch

from doubleblind_agent.broker_client import BrokerClient
from doubleblind_agent.models import Psm, Turn
from doubleblind_agent.prompts import CONTRACT_GUIDE, NEGOTIATOR
from doubleblind_contract.loader import parse_yaml
from doubleblind_contract.validate import validate_contract_data
from doubleblind_llm import LLMClient, LLMRequest, Role
from doubleblind_llm.errors import StructuredOutputError

RECENT_TURNS = 10
EXAMPLE_CONTRACT = files("doubleblind_agent").joinpath("example_contract.yaml").read_text("utf-8")
INTERNAL_FIXES = 4
SHOWN_PROBLEMS = 25


def patch_errors(contract: dict[str, Any], turn: Turn) -> list[str]:
    try:
        candidate = jsonpatch.apply_patch(contract, turn.patch_list())
    except (jsonpatch.JsonPatchException, jsonpatch.JsonPointerException) as error:
        return [f"patch: {str(error)[:300]}"]
    candidate = {**candidate, "version": contract["version"] + 1, "status": "proposed"}
    return [finding.render() for finding in validate_contract_data(candidate).errors]


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
    if feedback:
        parts.append(
            "Your previous attempt was rejected; fix these problems:\n"
            + "\n".join(f"- {item}" for item in feedback)
        )
    return [
        {"role": "system", "content": NEGOTIATOR.format(party=party, role=role)},
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
        "patches": turn.patch_list(),
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
    async def ask(conversation: list[dict[str, Any]], role_name: Role = Role.NEGOTIATOR) -> Turn:
        request = LLMRequest(
            role=role_name,
            messages=conversation,
            response_schema=Turn.model_json_schema(),
            party=party,
        )
        try:
            response = await llm.complete(request)
        except StructuredOutputError:
            response = await llm.complete(request)
        return Turn.model_validate(response.parsed)

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
        fix = await ask(conversation, Role.NEGOTIATOR_FIX)
        appended = turn.model_copy(update={"patches": [*turn.patches, *fix.patches]})
        replaced = turn.model_copy(update={"patches": fix.patches})
        turn = min(appended, replaced, key=lambda candidate: len(problems_of(candidate)))
    return turn_payload(context, turn)
