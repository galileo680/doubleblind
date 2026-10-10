from doubleblind_agent.models import Brief, Psm
from doubleblind_agent.prompts import BRIEF_WRITER
from doubleblind_llm import LLMClient, LLMRequest, Role


async def write_brief(
    llm: LLMClient,
    psm: Psm,
    party: str,
    role: str,
    synthetic: dict[str, list[str]],
    feedback: list[str],
) -> Brief:
    allowed = ", ".join([*synthetic["phones"], *synthetic["emails"]])
    messages = [
        {"role": "system", "content": BRIEF_WRITER.format(party=party, role=role)},
        {"role": "user", "content": f"Private system model:\n{psm.model_dump_json(indent=2)}"},
        {
            "role": "user",
            "content": "Phone numbers and e-mail addresses in examples may only be these "
            f"synthetic values: {allowed}.",
        },
    ]
    if feedback:
        rejected = "\n".join(f"- {reason}" for reason in feedback)
        messages.append(
            {
                "role": "user",
                "content": f"Your previous brief was rejected before leaving the company:\n"
                f"{rejected}\nWrite it again without these problems.",
            }
        )
    response = await llm.complete(
        LLMRequest(
            role=Role.BRIEF,
            messages=messages,
            response_schema=Brief.model_json_schema(),
            party=party,
        )
    )
    brief = Brief.model_validate(response.parsed)
    return brief.model_copy(update={"party": party, "role": role})
