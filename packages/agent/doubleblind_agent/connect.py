import argparse
import asyncio
import os
import re
import subprocess
import time
from pathlib import Path

import httpx

from doubleblind_agent.main import PATCH_FILE, PartyConfig, PartyRuntime
from doubleblind_llm.settings import BUNDLE_ROOT, load_dotenv, read_yaml
from doubleblind_sandbox.environment import remote_sandbox
from doubleblind_sandbox.images import CONFIG_FILE, contract_wheel, ensure_base_image, repo_plan

POLL_S = 5.0
CONTRACT_PACKAGE = BUNDLE_ROOT / "packages" / "contract"
COMPANY_KEYS = ("NEBIUS_API_KEY", "NEBIUS_PROJECT_ID")


def git(repo: Path, *arguments: str) -> str | None:
    result = subprocess.run(["git", *arguments], cwd=repo, capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else None


def commit_of(repo: Path) -> str:
    return git(repo, "rev-parse", "--short", "HEAD") or "no git"


def apply_hint(repo: Path, patch: Path) -> str:
    root = git(repo, "rev-parse", "--show-toplevel") or str(repo)
    folder = (git(repo, "rev-parse", "--show-prefix") or "").rstrip("/")
    option = f"--directory={folder} " if folder else ""
    return f"in {root} run: git apply {option}{patch.as_posix()}"


async def build_base(repo: Path, kind: str) -> str:
    provider = remote_sandbox(kind)()
    wheels, digest = contract_wheel(CONTRACT_PACKAGE)
    label = re.sub(r"[^a-z0-9]+", "-", repo.name.lower()).strip("-") or "repo"
    try:
        image = await ensure_base_image(provider, repo_plan(label, repo), wheels, digest)
    finally:
        await provider.aclose()
    return f"tag:{image.tag}"


def run_session(broker: str, key: str, session: dict[str, str], base: str, kind: str) -> bool:
    config = PartyConfig(
        broker,
        key,
        session["session_id"],
        session["party"],
        base,
        Path.cwd() / ".doubleblind" / session["session_id"],
    )
    runtime = PartyRuntime(config, remote_sandbox(kind))
    print(f"working on {session['session_id']} as {session['party']}")
    try:
        asyncio.run(runtime.run())
    except httpx.TransportError as error:
        print(f"lost the broker ({error}), resuming when it is back")
        return False
    except Exception as error:
        print(f"stopped: {type(error).__name__}: {error}")
    for line in runtime.log:
        print(f"[{session['party']}] {line}")
    patch = config.state_dir / PATCH_FILE
    if patch.is_file():
        print(f"our changes are in {patch}; review them, then {apply_hint(Path.cwd(), patch)}")
    return True


def connect(broker: str, key: str) -> None:
    repo, kind = Path.cwd(), os.environ.get("DOUBLEBLIND_SANDBOX", "tokenfactory")
    load_dotenv(repo / ".env")
    missing = [name for name in COMPANY_KEYS if not os.environ.get(name)]
    if missing:
        names = " and ".join(missing)
        raise SystemExit(f"put {names} of your company's Token Factory account in {repo / '.env'}")
    synthetic = read_yaml(repo / CONFIG_FILE).get("synthetic_test_data", {})
    print(f"building the base image of {repo.name} in {kind} sandboxes")
    base = asyncio.run(build_base(repo, kind))
    http = httpx.Client(
        base_url=f"{broker.rstrip('/')}/api/v1",
        headers={"Authorization": f"Bearer {key}"},
        timeout=30,
    )
    hello = http.post(
        "/agent/hello",
        json={"repo": repo.name, "commit": commit_of(repo), "synthetic_test_data": synthetic},
    )
    hello.raise_for_status()
    print(f"online in {hello.json()['integration']} as the {hello.json()['role']}")
    finished: set[str] = set()
    while True:
        try:
            response = http.get("/agent/session")
        except httpx.TransportError:
            time.sleep(POLL_S)
            continue
        response.raise_for_status()
        session = response.json() if response.status_code == 200 else None
        if session and session["session_id"] not in finished:
            if run_session(broker, key, session, base, kind):
                finished.add(session["session_id"])
                print("waiting for the next Start")
        else:
            time.sleep(POLL_S)


def main() -> None:
    parser = argparse.ArgumentParser(prog="doubleblind")
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("connect", help="connect this repository's agent")
    command.add_argument("--broker", required=True)
    command.add_argument("--key", required=True)
    arguments = parser.parse_args()
    connect(arguments.broker, arguments.key)


if __name__ == "__main__":
    main()
