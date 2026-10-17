import argparse
import asyncio
import json
from pathlib import Path

from doubleblind_llm.settings import load_dotenv
from doubleblind_sandbox.docker import docker_available
from doubleblind_sandbox.environment import BASE_IMAGES_FILES, remote_sandbox
from doubleblind_sandbox.images import BaseImage, contract_wheel, ensure_base_image, repo_plan
from doubleblind_sandbox.provider import SandboxError, SandboxProvider

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACT_PACKAGE = REPO_ROOT / "packages" / "contract"


def load_base_images(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    data: dict[str, str] = json.loads(path.read_text(encoding="utf-8"))
    return data


def build_provider(kind: str) -> SandboxProvider:
    if kind == "docker" and not docker_available():
        raise SandboxError("the Docker daemon is not running")
    if kind == "tokenfactory":
        load_dotenv(REPO_ROOT / ".env")
    return remote_sandbox(kind)()


async def build(
    demo_dir: Path, parties: list[str], force: bool, kind: str = "tokenfactory"
) -> list[BaseImage]:
    provider = build_provider(kind)
    wheels, wheels_digest = contract_wheel(CONTRACT_PACKAGE)
    try:
        plans = [repo_plan(party, demo_dir / party) for party in parties]
        results = await asyncio.gather(
            *(ensure_base_image(provider, plan, wheels, wheels_digest, force) for plan in plans),
            return_exceptions=True,
        )
        failures = [result for result in results if isinstance(result, BaseException)]
        if failures:
            raise SandboxError("\n\n".join(str(failure) for failure in failures))
        return [result for result in results if isinstance(result, BaseImage)]
    finally:
        await provider.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the base sandbox images of the demo parties"
    )
    parser.add_argument("--demo-dir", type=Path, default=REPO_ROOT / "demo")
    parser.add_argument("--party", action="append", choices=["spokes", "parcelly"])
    parser.add_argument("--force", action="store_true", help="rebuild even if the tag exists")
    parser.add_argument("--sandbox", choices=["tokenfactory", "docker"], default="tokenfactory")
    arguments = parser.parse_args()
    images = asyncio.run(
        build(
            arguments.demo_dir,
            arguments.party or ["spokes", "parcelly"],
            arguments.force,
            arguments.sandbox,
        )
    )
    images_file = BASE_IMAGES_FILES[arguments.sandbox]
    recorded = load_base_images(images_file)
    for image in images:
        recorded[image.party] = image.tag
        state = f"built for ${image.cost_usd:.4f}" if image.built else "already built"
        print(f"{image.party}: {image.tag} ({state})")
    images_file.parent.mkdir(parents=True, exist_ok=True)
    images_file.write_text(json.dumps(recorded, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
