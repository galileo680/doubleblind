import json
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

from doubleblind_agent.broker_client import BrokerClient, BrokerRejectedError
from doubleblind_agent.models import ClassifierVerdict
from doubleblind_agent.prompts import EGRESS_CLASSIFIER
from doubleblind_contract.guard_rules import SyntheticAllowlist, check_message, redact
from doubleblind_llm import LLMClient, LLMRequest, Role

MAX_ATTEMPTS = 3

Builder = Callable[[list[str]], Awaitable[dict[str, Any]]]
Sender = Callable[[dict[str, Any]], Awaitable[Any]]


class EgressBlockedError(Exception):
    pass


def redact_value(value: Any, redactions: Sequence[tuple[str, str]]) -> Any:
    if isinstance(value, str):
        return redact(value, redactions)
    if isinstance(value, dict):
        return {key: redact_value(item, redactions) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item, redactions) for item in value]
    return value


async def classify(llm: LLMClient, payload: Any, party: str) -> ClassifierVerdict:
    response = await llm.complete(
        LLMRequest(
            role=Role.GUARD,
            messages=[
                {"role": "system", "content": EGRESS_CLASSIFIER},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            response_schema=ClassifierVerdict.model_json_schema(),
            party=party,
        )
    )
    return ClassifierVerdict.model_validate(response.parsed)


class EgressGuard:
    def __init__(
        self,
        llm: LLMClient,
        broker: BrokerClient,
        allowlist: SyntheticAllowlist,
        repo_paths: Sequence[str],
        party: str,
    ) -> None:
        self.llm = llm
        self.broker = broker
        self.allowlist = allowlist
        self.repo_paths = list(repo_paths)
        self.party = party

    async def report(self, event_type: str, kind: str, reasons: Sequence[str]) -> None:
        reason = "; ".join(reasons)[:300] or event_type
        await self.broker.post_event({"type": event_type, "kind": kind, "reason": reason})

    async def send(self, kind: str, build: Builder, send: Sender) -> Any:
        feedback: list[str] = []
        for _ in range(MAX_ATTEMPTS):
            payload = await build(feedback)
            verdict = check_message(payload, self.allowlist, self.repo_paths)
            if verdict.blocked:
                await self.report("egress.blocked", kind, verdict.reasons)
                feedback = verdict.reasons
                continue
            payload = redact_value(payload, verdict.redactions)
            classified = await classify(self.llm, payload, self.party)
            if classified.verdict == "block":
                await self.report("egress.blocked", kind, ["blocked by the egress classifier"])
                feedback = classified.reasons
                continue
            redactions = [*verdict.redactions]
            if classified.verdict == "redact":
                redactions += [(item.text, item.replacement) for item in classified.redactions]
                payload = redact_value(payload, redactions)
            if redactions:
                await self.report(
                    "egress.redacted", kind, [f"{len(redactions)} fragments redacted"]
                )
            try:
                return await send(payload)
            except BrokerRejectedError as error:
                feedback = error.details or [str(error)]
        raise EgressBlockedError(f"{kind} was blocked {MAX_ATTEMPTS} times: {feedback}")
