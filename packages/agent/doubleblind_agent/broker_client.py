from typing import Any

import httpx

NEXT_ACTION_WAIT_S = 25.0
REJECTED = (409, 422)


class BrokerRejectedError(Exception):
    def __init__(self, code: str, message: str, details: Any) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.details = details if isinstance(details, list) else [f"{code}: {message}"]


class BrokerClient:
    def __init__(self, url: str, token: str, session_id: str) -> None:
        self.session = f"/api/v1/sessions/{session_id}"
        self.http = httpx.AsyncClient(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}"},
            timeout=NEXT_ACTION_WAIT_S + 30,
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def get(self, path: str) -> Any:
        response = await self.http.get(f"{self.session}{path}")
        response.raise_for_status()
        return response.json() if "json" in response.headers["content-type"] else response.text

    async def post(self, path: str, body: Any) -> Any:
        response = await self.http.post(f"{self.session}{path}", json=body)
        if response.status_code in REJECTED:
            error = response.json()
            raise BrokerRejectedError(error["code"], error["message"], error.get("details"))
        response.raise_for_status()
        return response.json()

    async def next_action(self) -> dict[str, Any]:
        action: dict[str, Any] = await self.get("/next-action")
        return action

    async def post_brief(self, brief: dict[str, Any]) -> Any:
        return await self.post("/briefs", brief)

    async def post_turn(self, turn: dict[str, Any]) -> Any:
        return await self.post("/turns", turn)

    async def post_test_run(self, run: dict[str, Any]) -> Any:
        return await self.post("/test-runs", run)

    async def post_contract_gap(self, gap: dict[str, Any]) -> Any:
        return await self.post("/contract-gap", gap)

    async def post_event(self, event: dict[str, Any]) -> Any:
        return await self.post("/events", event)
