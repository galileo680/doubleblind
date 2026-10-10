import logging
from collections.abc import Sequence
from typing import Protocol

from doubleblind_broker.presets import Preset
from doubleblind_llm import LLMClient, LLMRequest, Role

logger = logging.getLogger(__name__)

ANSWER_SCHEMA = {
    "type": "object",
    "required": ["answer"],
    "additionalProperties": False,
    "properties": {"answer": {"type": "string", "minLength": 1}},
}


class HumanSimulator(Protocol):
    async def answer(
        self, question: str, options: Sequence[str], default: str | None, persona: str
    ) -> str: ...


def fallback_answer(options: Sequence[str], default: str | None) -> str:
    if default:
        return default
    if options:
        return options[0]
    return "yes"


class DefaultOptionSimulator:
    async def answer(
        self, question: str, options: Sequence[str], default: str | None, persona: str
    ) -> str:
        return fallback_answer(options, default)


class LLMHumanSimulator:
    def __init__(self, client: LLMClient) -> None:
        self.client = client

    async def answer(
        self, question: str, options: Sequence[str], default: str | None, persona: str
    ) -> str:
        listed = "\n".join(f"- {option}" for option in options) or "- (free answer)"
        request = LLMRequest(
            role=Role.HUMAN_SIM,
            messages=[
                {
                    "role": "system",
                    "content": (
                        f"You are a {persona}. Answer the integration question briefly. "
                        "Prefer the default option when it is safe for your business."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Question: {question}\nOptions:\n{listed}\nDefault: {default}",
                },
            ],
            response_schema=ANSWER_SCHEMA,
        )
        try:
            response = await self.client.complete(request)
        except Exception as error:
            logger.warning("human simulator failed, using the default option: %s", error)
            return fallback_answer(options, default)
        answer = (response.parsed or {}).get("answer")
        return answer if isinstance(answer, str) and answer else fallback_answer(options, default)


async def autopilot_answer(
    preset: Preset,
    simulator: HumanSimulator,
    question: str,
    options: Sequence[str],
    default: str | None,
    role: str,
) -> str:
    preset_answer = preset.preset_answer(question)
    if preset_answer is not None:
        return preset_answer
    persona = preset.personas.consumer if role == "consumer" else preset.personas.provider
    return await simulator.answer(question, options, default, persona)
