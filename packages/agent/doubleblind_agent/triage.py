import json
from typing import Any

from doubleblind_agent.models import TriageResult
from doubleblind_agent.prompts import E2E_MISSING_STEP, TRIAGE
from doubleblind_agent.stable import stable
from doubleblind_llm import LLMClient, LLMRequest, Role

LOG_LINES = 40


def failing(report: dict[str, Any]) -> list[dict[str, Any]]:
    return [item for item in report["scenarios"] if item["status"] != "passed"]


def triage_input(report: dict[str, Any]) -> str:
    failures = [
        {
            "scenario": item["id"],
            "title": item["title"],
            "status": item["status"],
            "failures": item["failures"][:5],
            "app_log_tail": "\n".join(item.get("app_log_tail", "").splitlines()[-LOG_LINES:]),
        }
        for item in failing(report)
    ]
    return stable(json.dumps(failures, ensure_ascii=False))


def e2e_triage_input(feedback: dict[str, Any], side: str) -> str:
    return stable(
        json.dumps(
            {
                "our_side": side,
                "failing_e2e_scenarios": failing(feedback["report"]),
                "gateway_traffic": feedback["gateway"],
            },
            ensure_ascii=False,
        )
    )


async def triage(llm: LLMClient, report: dict[str, Any], party: str) -> TriageResult:
    return await classify(llm, triage_input(report), party)


async def triage_e2e(
    llm: LLMClient, feedback: dict[str, Any], party: str, side: str
) -> TriageResult:
    return await classify(llm, e2e_triage_input(feedback, side), party, E2E_MISSING_STEP)


async def classify(llm: LLMClient, content: str, party: str, rules: str = "") -> TriageResult:
    system = TRIAGE.format(party=party) + (f"\n{rules}" if rules else "")
    response = await llm.complete(
        LLMRequest(
            role=Role.TRIAGE,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": content},
            ],
            response_schema=TriageResult.model_json_schema(),
            party=party,
        )
    )
    return TriageResult.model_validate(response.parsed)
