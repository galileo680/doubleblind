import asyncio
import base64
import hashlib
import json
import os
import time
from collections.abc import AsyncIterator, Mapping
from typing import Any

import httpx

from doubleblind_sandbox.costs import LedgerCosts
from doubleblind_sandbox.provider import (
    DEFAULT_TIMEOUT_S,
    REPO_DIR,
    SERVICE_TIMEOUT_S,
    ExecResult,
    SandboxError,
    ServiceHandle,
    Workspace,
)

BASE_URL = "https://api.tokenfactory.nebius.com/sandboxes"
FINAL_STATUSES = frozenset({"SUCCESS", "FAILED", "CANCELLED"})
MAX_CONCURRENT_INSTANCES = 40
OUTPUT_LIMIT = 1_000_000
POLL_START_S = 0.3
POLL_MAX_S = 2.0
FILE_MODE = "0644"


def decode_stream(stream: Mapping[str, Any] | None) -> str:
    if not stream:
        return ""
    value = str(stream.get("value") or "")
    if stream.get("encoding") == "base64":
        return base64.b64decode(value).decode("utf-8", errors="replace")
    return value


class TokenFactorySandboxProvider:
    name = "tokenfactory"
    python = "python3"

    def __init__(
        self,
        api_key: str,
        project_id: str,
        costs: LedgerCosts | None = None,
        base_url: str = BASE_URL,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self.client = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}", "Project": project_id},
            timeout=60.0,
            transport=transport,
        )
        self.costs = costs
        self.semaphore = asyncio.Semaphore(MAX_CONCURRENT_INSTANCES)
        self.sleep = sleep

    @classmethod
    def from_env(
        cls, costs: LedgerCosts | None = None, prefix: str = ""
    ) -> "TokenFactorySandboxProvider":
        api_key = os.environ.get(f"{prefix}SANDBOX_API_KEY") or os.environ.get("NEBIUS_API_KEY")
        project = os.environ.get(f"{prefix}SANDBOX_PROJECT_ID") or os.environ.get(
            "NEBIUS_PROJECT_ID"
        )
        if not api_key or not project:
            raise SandboxError(
                "set NEBIUS_API_KEY and NEBIUS_PROJECT_ID to use Token Factory Sandboxes"
            )
        return cls(api_key, project, costs)

    async def aclose(self) -> None:
        await self.client.aclose()

    async def request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        response = await self.client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise SandboxError(
                f"{method} {path} failed with {response.status_code}: {response.text[:300]}"
            )
        return response

    async def open_workspace(self, party: str, base: str) -> Workspace:
        return Workspace(party, base, [base])

    async def upload(self, content: bytes) -> str:
        digest = hashlib.sha256(content).hexdigest()
        existing = await self.client.get(f"/v1/files/{digest}")
        if existing.status_code == 200:
            return str(existing.json()["uuid"])
        response = await self.request(
            "POST",
            "/v1/files",
            content=content,
            headers={"Content-Type": "application/octet-stream"},
        )
        return str(response.json()["uuid"])

    async def file_specs(self, files: Mapping[str, bytes] | None) -> dict[str, dict[str, Any]]:
        specs: dict[str, dict[str, Any]] = {}
        for path, content in (files or {}).items():
            specs[path] = {
                "uuid": await self.upload(content),
                "mode": FILE_MODE,
                "uid": 0,
                "gid": 0,
            }
        return specs

    async def spawn(
        self,
        image: str,
        command: str,
        *,
        persist: bool,
        network: bool,
        timeout_s: int,
        files: Mapping[str, bytes] | None,
        cwd: str,
        env: Mapping[str, str] | None,
    ) -> str:
        body = {
            "command": command,
            "image": image,
            "hostname": "sandbox",
            "args": [],
            "shell": True,
            "env": dict(env or {}),
            "cwd": cwd,
            "disposable": not persist,
            "timeout": timeout_s,
            "files": await self.file_specs(files),
            "truncate_output_at": OUTPUT_LIMIT,
            "networking": {"enabled": network},
        }
        response = await self.request("POST", "/v1/instances", json=body)
        return str(response.json()["uuid"])

    async def wait(self, operation: str, timeout_s: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_s
        interval = POLL_START_S
        while True:
            data: dict[str, Any] = (await self.request("GET", f"/v1/operations/{operation}")).json()
            if data.get("status") in FINAL_STATUSES:
                return data
            if time.monotonic() > deadline:
                await self.client.delete(f"/v1/operations/{operation}")
                raise SandboxError(f"operation {operation} did not finish within {timeout_s:.0f} s")
            await self.sleep(interval)
            interval = min(interval * 1.5, POLL_MAX_S)

    def to_result(self, data: Mapping[str, Any], duration_s: float) -> ExecResult:
        metadata = data.get("metadata") or {}
        result = metadata.get("result") or {}
        state = result.get("state") or {}
        if data.get("status") != "SUCCESS" and not state:
            return ExecResult(
                -1, "", str(data.get("error") or data.get("status")), duration_s=duration_s
            )
        new_image = (data.get("result") or {}).get("image")
        return ExecResult(
            exit_code=int(state.get("exit_code", -1)),
            stdout=decode_stream(result.get("stdout")),
            stderr=decode_stream(result.get("stderr")),
            timed_out=bool(state.get("timed_out", False)),
            duration_s=duration_s,
            cost_usd=float((result.get("resources") or {}).get("cost") or 0.0),
            new_head=str(new_image) if new_image else None,
        )

    async def run(
        self,
        workspace: Workspace,
        command: str,
        *,
        persist: bool = False,
        network: bool = False,
        timeout_s: int = DEFAULT_TIMEOUT_S,
        files: Mapping[str, bytes] | None = None,
        cwd: str = REPO_DIR,
        env: Mapping[str, str] | None = None,
    ) -> ExecResult:
        if self.costs is not None:
            self.costs.check()
        started = time.monotonic()
        async with self.semaphore:
            operation = await self.spawn(
                workspace.head,
                command,
                persist=persist,
                network=network,
                timeout_s=timeout_s,
                files=files,
                cwd=cwd,
                env=env,
            )
            data = await self.wait(operation, timeout_s + 120)
        result = self.to_result(data, time.monotonic() - started)
        if self.costs is not None:
            self.costs.record(workspace.party, result.cost_usd, command)
        if persist and result.ok and result.new_head:
            workspace.advance(result.new_head)
        return result

    async def write_files(self, workspace: Workspace, files: Mapping[str, bytes]) -> ExecResult:
        result = await self.run(workspace, "true", persist=True, files=files, cwd="/")
        if not result.ok:
            raise SandboxError(f"writing files failed: {result.output()[:300]}")
        return result

    async def resolve(self, head: str) -> str:
        if not head.startswith("tag:"):
            return head
        uuid = await self.find_tag(head.removeprefix("tag:"))
        if uuid is None:
            raise SandboxError(f"no image with tag {head.removeprefix('tag:')}")
        return uuid

    async def read_file(self, workspace: Workspace, path: str) -> bytes:
        uuid = await self.resolve(workspace.head)
        response = await self.client.get(f"/v1/inspect/{uuid}/download", params={"path": path})
        if response.status_code == 200:
            return response.content
        result = await self.run(workspace, f"base64 -w0 '{path}'", cwd="/")
        if not result.ok:
            raise SandboxError(f"cannot read {path}: {result.stderr[:200]}")
        return base64.b64decode(result.stdout)

    async def find_tag(self, tag: str) -> str | None:
        response = await self.client.get("/v1/inspect/", params={"tag": tag}, follow_redirects=True)
        if response.status_code == 200:
            return str(response.json()["uuid"])
        return None

    async def tag(self, workspace: Workspace, tag: str) -> None:
        uuid = await self.resolve(workspace.head)
        await self.request("PATCH", f"/v1/images/{uuid}/tag", json={"tag": tag})

    async def import_image(self, url: str, tag: str, timeout_s: int = 600) -> str:
        existing = await self.find_tag(tag)
        if existing is not None:
            return existing
        response = await self.request(
            "POST",
            "/v1/images/import",
            json={"registry": {"url": url}, "tag": tag, "timeout": timeout_s},
        )
        data = await self.wait(str(response.json()["uuid"]), timeout_s + 60)
        if data.get("status") != "SUCCESS":
            raise SandboxError(f"importing {url} failed: {data.get('error')}")
        found = await self.find_tag(tag)
        if found is None:
            raise SandboxError(f"imported {url} but tag {tag} is missing")
        return found

    async def start_service(
        self,
        workspace: Workspace,
        command: str,
        *,
        network: bool = True,
        timeout_s: int = SERVICE_TIMEOUT_S,
        env: Mapping[str, str] | None = None,
        files: Mapping[str, bytes] | None = None,
    ) -> ServiceHandle:
        if self.costs is not None:
            self.costs.check()
        operation = await self.spawn(
            workspace.head,
            command,
            persist=False,
            network=network,
            timeout_s=timeout_s,
            files=files,
            cwd=REPO_DIR,
            env=env,
        )
        return ServiceHandle(operation, workspace.party)

    async def service_output(
        self, handle: ServiceHandle, timeout_s: float = 30.0
    ) -> AsyncIterator[str]:
        async with self.client.stream(
            "GET",
            f"/v1/operations/{handle.id}/events",
            params={"follow": 1},
            timeout=timeout_s,
        ) as response:
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                try:
                    event = json.loads(line.removeprefix("data:").strip())
                except ValueError:
                    continue
                if event.get("type") in ("stdout", "stderr"):
                    data = event.get("data")
                    yield data if isinstance(data, str) else json.dumps(data)

    async def stop_service(self, handle: ServiceHandle) -> float:
        await self.client.delete(f"/v1/operations/{handle.id}")
        data = await self.wait(handle.id, 60)
        cost = self.to_result(data, 0.0).cost_usd
        if self.costs is not None:
            self.costs.record(handle.party, cost, "service")
        return cost
