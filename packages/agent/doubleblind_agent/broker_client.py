from typing import Any

import httpx

NEXT_ACTION_WAIT_S = 25.0


class BrokerRejectedError(Exception):
    def __init__(self, code: str, message: str, details: list[str]) -> None:
        super().__init__(f"{code}: {message}")
        self.details = details


class BrokerClient:
    def __init__(self, url: str, token: str, session_id: str) -> None:
        self.http = httpx.AsyncClient(
            base_url=f"{url.rstrip('/')}/api/v1/sessions/{session_id}",
            headers={"Authorization": f"Bearer {token}"},
            timeout=NEXT_ACTION_WAIT_S + 30,
        )

    async def aclose(self) -> None:
        await self.http.aclose()

    async def post(self, path: str, body: Any) -> Any:
        response = await self.http.post(path, json=body)
        if response.status_code == 422 and response.json().get("code") == "EGRESS_BLOCKED":
            error = response.json()
            raise BrokerRejectedError(error["code"], error["message"], error.get("details") or [])
        response.raise_for_status()
        return response.json()

    async def next_action(self) -> dict[str, Any]:
        response = await self.http.get("/next-action")
        response.raise_for_status()
        action: dict[str, Any] = response.json()
        return action

    async def post_brief(self, brief: dict[str, Any]) -> Any:
        return await self.post("/briefs", brief)

    async def post_event(self, event: dict[str, Any]) -> Any:
        return await self.post("/events", event)
