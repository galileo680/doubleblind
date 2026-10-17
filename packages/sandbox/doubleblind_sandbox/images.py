import hashlib
import subprocess
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

from doubleblind_llm.settings import read_yaml
from doubleblind_sandbox.provider import REPO_DIR, SandboxError, SandboxProvider

TOOLS_DIR = "/opt/doubleblind"
RECIPE_PATH = f"{TOOLS_DIR}/recipe.sh"
WHEELS_DIR = f"{TOOLS_DIR}/wheels"
BASE_IMAGES = {"tokenfactory": "tag:python:3.13-slim", "docker": "tag:python:3.13.13-slim"}
BUILD_TIMEOUT_S = 1500
SKIPPED_PARTS = frozenset(
    {
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
        ".git",
        ".doubleblind",
    }
)
SKIPPED_SUFFIXES = (".db", ".pyc")
CONFIG_FILE = "doubleblind.yaml"
SKIPPED_NAMES = frozenset({"doubleblind.binding.e2e.yaml", CONFIG_FILE})
NODE_INSTALLER = Path(__file__).with_name("install_node.py")
RECIPE = """set -eu
export DEBIAN_FRONTEND=noninteractive
export HOME=/root
export PIP_DISABLE_PIP_VERSION_CHECK=1
apt-get update -qq
apt-get install -y -qq --no-install-recommends git ripgrep ca-certificates xz-utils > /dev/null
pip install --quiet --no-cache-dir --root-user-action=ignore {wheels}/*.whl
git config --global user.email agent@doubleblind.local
git config --global user.name "DoubleBlind agent"
git config --global init.defaultBranch main
cd {repo}
{install}
git init -q
git add -A
git commit -q -m "initial state"
"""


@dataclass(frozen=True)
class BasePlan:
    party: str
    repo_dir: Path
    recipe: bytes
    tools: Mapping[str, Path] = field(default_factory=dict)


def repo_plan(party: str, repo_dir: Path) -> BasePlan:
    install = read_yaml(repo_dir / CONFIG_FILE)["install"].strip()
    recipe = RECIPE.format(wheels=WHEELS_DIR, repo=REPO_DIR, install=install)
    return BasePlan(
        party, repo_dir, recipe.encode(), {f"{TOOLS_DIR}/install_node.py": NODE_INSTALLER}
    )


def contract_wheel(package_dir: Path) -> tuple[dict[str, bytes], str]:
    with tempfile.TemporaryDirectory() as directory:
        subprocess.run(
            [sys.executable, "-m", "pip", "wheel", "--no-deps", "-q", "-w", directory, package_dir],
            check=True,
        )
        wheels = {path.name: path.read_bytes() for path in Path(directory).glob("*.whl")}
    return wheels, directory_hash(package_dir / "doubleblind_contract")


@dataclass(frozen=True)
class BaseImage:
    party: str
    tag: str
    built: bool
    cost_usd: float = 0.0


def iter_repo_files(repo_dir: Path) -> Iterable[Path]:
    for path in sorted(repo_dir.rglob("*")):
        relative = path.relative_to(repo_dir)
        if not path.is_file() or SKIPPED_PARTS.intersection(relative.parts):
            continue
        if path.suffix in SKIPPED_SUFFIXES or path.name in SKIPPED_NAMES:
            continue
        yield path


def repo_files(repo_dir: Path) -> dict[str, bytes]:
    return {
        f"{REPO_DIR}/{path.relative_to(repo_dir).as_posix()}": path.read_bytes()
        for path in iter_repo_files(repo_dir)
    }


def content_hash(files: Mapping[str, bytes], extra: Iterable[str] = ()) -> str:
    digest = hashlib.sha256()
    for path in sorted(files):
        digest.update(path.encode())
        digest.update(hashlib.sha256(files[path]).digest())
    for item in extra:
        digest.update(item.encode())
    return digest.hexdigest()


def directory_hash(directory: Path) -> str:
    files = {
        path.relative_to(directory).as_posix(): path.read_bytes()
        for path in iter_repo_files(directory)
    }
    return content_hash(files)


def base_tag(party: str, digest: str) -> str:
    return f"doubleblind-{party}-base-{digest[:12]}"


def build_files(plan: BasePlan, wheels: Mapping[str, bytes]) -> dict[str, bytes]:
    files = repo_files(plan.repo_dir)
    files[RECIPE_PATH] = plan.recipe
    for target, source in plan.tools.items():
        files[target] = source.read_bytes()
    for name, content in wheels.items():
        files[f"{WHEELS_DIR}/{name}"] = content
    return files


async def ensure_base_image(
    provider: SandboxProvider,
    plan: BasePlan,
    wheels: Mapping[str, bytes],
    wheels_digest: str,
    force: bool = False,
) -> BaseImage:
    files = build_files(plan, wheels)
    hashed = {path: content for path, content in files.items() if not path.startswith(WHEELS_DIR)}
    start = BASE_IMAGES[provider.name]
    tag = base_tag(plan.party, content_hash(hashed, [wheels_digest, start]))
    if not force and await provider.find_tag(tag) is not None:
        return BaseImage(plan.party, tag, built=False)
    workspace = await provider.open_workspace(plan.party, start)
    result = await provider.run(
        workspace,
        f"sh {RECIPE_PATH}",
        persist=True,
        network=True,
        timeout_s=BUILD_TIMEOUT_S,
        files=files,
        cwd="/",
    )
    if not result.ok:
        raise SandboxError(
            f"building the {plan.party} base image failed:\n{result.output()[-2000:]}"
        )
    await provider.tag(workspace, tag)
    return BaseImage(plan.party, tag, built=True, cost_usd=result.cost_usd)
