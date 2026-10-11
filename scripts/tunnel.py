import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import httpx
from demo_workspace import REPO_ROOT

TUNNEL_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
START_TIMEOUT_S = 60.0
READY_TIMEOUT_S = 90.0


def cloudflared() -> str:
    configured = os.environ.get("DOUBLEBLIND_CLOUDFLARED")
    if configured:
        return configured
    found = shutil.which("cloudflared")
    if found:
        return found
    for name in ("cloudflared.exe", "cloudflared"):
        local = REPO_ROOT / "volumes" / "tools" / name
        if local.is_file():
            return str(local)
    raise SystemExit(
        "Token Factory sandboxes reach the gateway only through a public URL: set "
        "BROKER_PUBLIC_URL or install cloudflared (or DOUBLEBLIND_CLOUDFLARED=<path>)"
    )


@contextmanager
def public_url(port: int) -> Iterator[str]:
    configured = os.environ.get("BROKER_PUBLIC_URL")
    if configured:
        yield configured
        return
    process = subprocess.Popen(
        [cloudflared(), "tunnel", "--no-autoupdate", "--url", f"http://127.0.0.1:{port}"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )
    found: list[str] = []

    def watch() -> None:
        assert process.stderr is not None
        for line in process.stderr:
            match = TUNNEL_URL.search(line)
            if match and not found:
                found.append(match.group(0))

    threading.Thread(target=watch, daemon=True).start()
    deadline = time.monotonic() + START_TIMEOUT_S
    while not found and time.monotonic() < deadline and process.poll() is None:
        time.sleep(0.5)
    if not found:
        process.terminate()
        raise SystemExit("cloudflared did not report a public URL")
    os.environ["BROKER_PUBLIC_URL"] = found[0]
    try:
        yield found[0]
    finally:
        os.environ.pop("BROKER_PUBLIC_URL", None)
        process.terminate()
        process.wait(timeout=15)


def wait_until_reachable(url: str) -> None:
    deadline = time.monotonic() + READY_TIMEOUT_S
    while time.monotonic() < deadline:
        try:
            if httpx.get(f"{url}/health", timeout=10.0).status_code == 200:
                return
        except httpx.HTTPError:
            pass
        time.sleep(2.0)
    raise SystemExit(f"the broker is not reachable through {url}")
