import asyncio
import base64
import binascii
import contextlib
import os
import re
from collections.abc import Awaitable, Callable, Mapping

import httpx

from doubleblind_sandbox.provider import ProbeResult, ServiceHandle, ServiceSandbox, Workspace
from doubleblind_sandbox.tokenfactory import TokenFactorySandboxProvider

MODELS_URL = "https://api.tokenfactory.nebius.com/v1/models"
PROBE_PORT = 8080
IP_LINE = re.compile(r"PROBE_IP (\d+\.\d+\.\d+\.\d+)")
DNS_COMMAND = (
    'python3 -c "import urllib.request; '
    "urllib.request.urlopen('https://example.com', timeout=10)\""
)
DNS_ERRORS = ("Name or service not known", "Temporary failure in name resolution", "gaierror")
LISTENER_COMMAND = (
    'python3 -c "import socket, http.server; '
    "s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM); s.connect(('10.255.255.255', 1)); "
    "print('PROBE_IP', s.getsockname()[0], flush=True); "
    f"http.server.ThreadingHTTPServer(('0.0.0.0', {PROBE_PORT}), "
    'http.server.SimpleHTTPRequestHandler).serve_forever()"'
)
SERVICE_WAIT_S = 120.0

ProbeFactory = Callable[[], ServiceSandbox]


def connect_command(address: str) -> str:
    return (
        'python3 -c "import urllib.request; '
        f"urllib.request.urlopen('http://{address}:{PROBE_PORT}', timeout=8)\""
    )


def passed_if(name: str, expected: str, actual: str, ok: bool) -> ProbeResult:
    return ProbeResult(name, expected, actual, "passed" if ok else "failed")


async def probe_dns(sandbox: ServiceSandbox, workspace: Workspace) -> ProbeResult:
    result = await sandbox.run(workspace, DNS_COMMAND, timeout_s=60)
    output = result.output()
    failed_on_dns = not result.ok and any(marker in output for marker in DNS_ERRORS)
    actual = next((marker for marker in DNS_ERRORS if marker in output), output.strip()[-160:])
    return passed_if(
        f"dns from the {workspace.party} sandbox",
        "DNS error",
        actual or f"exit {result.exit_code}",
        failed_on_dns,
    )


async def probe_listing(sandbox: ServiceSandbox, workspace: Workspace) -> ProbeResult:
    result = await sandbox.run(workspace, "ls /workspace", cwd="/", timeout_s=60)
    entries = result.stdout.split()
    return passed_if(
        f"ls /workspace in the {workspace.party} sandbox",
        "repo",
        " ".join(entries) or result.output().strip()[-160:],
        result.ok and entries == ["repo"],
    )


def find_address(chunk: str) -> str | None:
    candidates = [chunk]
    with contextlib.suppress(binascii.Error, ValueError):
        candidates.append(base64.b64decode(chunk, validate=True).decode("utf-8", "replace"))
    for candidate in candidates:
        match = IP_LINE.search(candidate)
        if match:
            return match.group(1)
    return None


async def listener_address(
    sandbox: TokenFactorySandboxProvider, handle: ServiceHandle
) -> str | None:
    async def read() -> str | None:
        async for chunk in sandbox.service_output(handle, timeout_s=SERVICE_WAIT_S):
            address = find_address(chunk)
            if address:
                return address
        return None

    try:
        return await asyncio.wait_for(read(), SERVICE_WAIT_S)
    except (TimeoutError, httpx.HTTPError):
        return None


async def probe_no_route(
    sandbox: ServiceSandbox, consumer: Workspace, provider: Workspace
) -> ProbeResult:
    name = f"route from the {consumer.party} E2E sandbox to the {provider.party} E2E sandbox"
    if not isinstance(sandbox, TokenFactorySandboxProvider):
        return ProbeResult(name, "No route to host", "needs Token Factory sandboxes", "skipped")
    handle = await sandbox.start_service(provider, LISTENER_COMMAND, timeout_s=300)
    try:
        address = await listener_address(sandbox, handle)
        if address is None:
            return ProbeResult(name, "No route to host", "the listener did not start", "failed")
        result = await sandbox.run(consumer, connect_command(address), network=True, timeout_s=60)
    finally:
        await sandbox.stop_service(handle)
    reached = result.ok
    actual = "connected" if reached else (result.output().strip().splitlines() or ["error"])[-1]
    return passed_if(name, "No route to host", f"{address}: {actual[-160:]}", not reached)


async def probe_credentials(foreign_tag: str | None) -> ProbeResult:
    name = "spokes credentials reading a parcelly image"
    key = os.environ.get("SPOKES_SANDBOX_API_KEY")
    project = os.environ.get("SPOKES_SANDBOX_PROJECT_ID")
    shared = project is not None and project == os.environ.get("PARCELLY_SANDBOX_PROJECT_ID")
    if not key or not project or shared or foreign_tag is None:
        return ProbeResult(name, "denied", "one Token Factory project for both parties", "skipped")
    sandbox = TokenFactorySandboxProvider(key, project)
    try:
        found = await sandbox.find_tag(foreign_tag)
    except httpx.HTTPError as error:
        found = None
        actual = f"error: {error}"
    else:
        actual = "visible" if found else "denied"
    finally:
        await sandbox.aclose()
    return passed_if(name, "denied", actual, found is None)


async def probe_models(api_key: str | None) -> ProbeResult:
    name = "Token Factory /v1/models from the party runtime"
    if not api_key:
        return ProbeResult(name, "200", "NEBIUS_API_KEY is not set", "failed")
    async with httpx.AsyncClient(timeout=30.0) as http:
        try:
            response = await http.get(MODELS_URL, headers={"Authorization": f"Bearer {api_key}"})
        except httpx.HTTPError as error:
            return ProbeResult(name, "200", f"error: {error}", "failed")
    return passed_if(name, "200", str(response.status_code), response.status_code == 200)


class SandboxIsolationProbe:
    def __init__(self, factory: ProbeFactory, bases: Mapping[str, str]) -> None:
        self.factory = factory
        self.bases = dict(bases)

    async def run(self) -> list[ProbeResult]:
        sandbox = self.factory()
        try:
            consumer = await sandbox.open_workspace("spokes", self.bases["spokes"])
            provider = await sandbox.open_workspace("parcelly", self.bases["parcelly"])
            probes: list[Callable[[], Awaitable[ProbeResult]]] = [
                lambda: probe_dns(sandbox, consumer),
                lambda: probe_listing(sandbox, consumer),
                lambda: probe_credentials(self.bases["parcelly"].removeprefix("tag:")),
                lambda: probe_no_route(sandbox, consumer, provider),
                lambda: probe_models(os.environ.get("NEBIUS_API_KEY")),
            ]
            return list(await asyncio.gather(*(probe() for probe in probes)))
        finally:
            await sandbox.aclose()
