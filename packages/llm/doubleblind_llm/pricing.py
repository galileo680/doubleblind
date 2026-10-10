import json
import math
from typing import Any

from doubleblind_llm.domain import Usage
from doubleblind_llm.settings import Price

CHARS_PER_TOKEN = 3.5
MILLION = 1_000_000


def cost_usd(price: Price, usage: Usage) -> float:
    return (
        usage.prompt_tokens * price.input_per_m + usage.completion_tokens * price.output_per_m
    ) / MILLION


def estimate_prompt_tokens(payload: dict[str, Any]) -> int:
    relevant = {
        key: payload[key] for key in ("messages", "tools", "response_format") if key in payload
    }
    text = json.dumps(relevant, ensure_ascii=False, default=str)
    return math.ceil(len(text) / CHARS_PER_TOKEN)
