import json
from typing import Any

from doubleblind_agent.models import TriageResult
from doubleblind_agent.prompts import TRIAGE
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


async def triage(llm: LLMClient, report: dict[str, Any], party: str) -> TriageResult:
    response = await llm.complete(
        LLMRequest(
            role=Role.TRIAGE,
            messages=[
                {"role": "system", "content": TRIAGE.format(party=party)},
                {"role": "user", "content": triage_input(report)},
            ],
            response_schema=TriageResult.model_json_schema(),
            party=party,
        )
    )
    return TriageResult.model_validate(response.parsed)
