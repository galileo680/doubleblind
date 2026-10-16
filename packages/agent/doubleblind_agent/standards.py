import json
import os
from pathlib import Path
from typing import Any

import httpx

TAVILY_URL = "https://api.tavily.com/search"
TAVILY_KEY_ENV = "TAVILY_API_KEY"
RECORDING_ENV = "DOUBLEBLIND_TAVILY_RECORDING"
ENABLED_ENV = "DOUBLEBLIND_TAVILY"
MAX_SOURCES = 2
ANSWER_CHARS = 600


class StandardLookupError(Exception):
    pass


def enabled() -> bool:
    return os.environ.get(ENABLED_ENV) == "1"


def recorded(path: Path) -> dict[str, dict[str, Any] | None]:
    if not path.is_file():
        return {}
    items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]
    return {item["query"]: item["result"] for item in items}


async def search(query: str, api_key: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=30.0) as http:
        response = await http.post(
            TAVILY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={"query": query, "include_answer": True, "max_results": MAX_SOURCES},
        )
    response.raise_for_status()
    data = response.json()
    return {
        "query": query,
        "answer": (data.get("answer") or "")[:ANSWER_CHARS],
        "sources": [item["url"] for item in data.get("results", [])[:MAX_SOURCES]],
    }


async def lookup_standard(query: str) -> dict[str, Any] | None:
    mode = os.environ.get("DOUBLEBLIND_LLM_MODE", "live")
    path = Path(os.environ.get(RECORDING_ENV, ""))
    saved = recorded(path) if mode in ("record", "replay") and path.name else {}
    if query in saved:
        return saved[query]
    if mode == "replay":
        raise StandardLookupError(f"no recorded Tavily result for '{query}'")
    api_key = os.environ.get(TAVILY_KEY_ENV)
    result = await search(query, api_key) if api_key else None
    if mode == "record" and path.name:
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"query": query, "result": result}, ensure_ascii=False) + "\n")
    return result
