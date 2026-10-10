import json
import os
from collections.abc import Callable

from doubleblind_llm.settings import PACKAGE_REPO_ROOT
from doubleblind_sandbox.costs import LedgerCosts
from doubleblind_sandbox.docker import DockerSandboxProvider
from doubleblind_sandbox.provider import ServiceSandbox
from doubleblind_sandbox.tokenfactory import TokenFactorySandboxProvider

BASE_IMAGES_FILES = {
    "tokenfactory": PACKAGE_REPO_ROOT / "volumes" / "base-images.json",
    "docker": PACKAGE_REPO_ROOT / "volumes" / "base-images.docker.json",
}


def remote_sandbox(kind: str) -> Callable[[], ServiceSandbox]:
    if kind == "docker":
        return DockerSandboxProvider
    return lambda: TokenFactorySandboxProvider.from_env(LedgerCosts.from_env(PACKAGE_REPO_ROOT))


def recorded_bases(kind: str) -> dict[str, str] | None:
    path = BASE_IMAGES_FILES.get(kind)
    if path is None or not path.is_file():
        return None
    tags: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    return {party: f"tag:{tag}" for party, tag in tags.items()}


def gateway_url(kind: str, port: int | str) -> str | None:
    public = os.environ.get("BROKER_PUBLIC_URL")
    if public:
        return public
    if kind == "tokenfactory":
        return None
    host = "host.docker.internal" if kind == "docker" else "127.0.0.1"
    return f"http://{host}:{port}"
