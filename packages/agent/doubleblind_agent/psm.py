import json
from dataclasses import dataclass
from pathlib import Path

import doubleblind_agent.collect_files as collector
from doubleblind_agent.models import Psm
from doubleblind_agent.prompts import PSM_BUILDER
from doubleblind_llm import LLMClient, LLMRequest, Role
from doubleblind_sandbox.provider import SandboxProvider, Workspace, repo_path

COLLECTOR_SOURCE = Path(collector.__file__).read_bytes()
COLLECTOR_FILE = ".doubleblind/collect_files.py"
SCOPE = "Integration scope agreed by both companies before the agents started: "


@dataclass
class RepoSnapshot:
    tree: list[str]
    files: list[dict[str, str]]

    @property
    def tokens(self) -> int:
        return round(sum(len(item["content"]) for item in self.files) / collector.CHARS_PER_TOKEN)

    def as_prompt(self, role: str, scope: str | None) -> str:
        sections = [f"Role in the integration: {role}"]
        if scope:
            sections.append(f"{SCOPE}{scope}")
        sections += ["Repository tree:", *self.tree]
        for item in self.files:
            sections += [f"\n=== {item['path']} ===", item["content"]]
        return "\n".join(sections)


async def snapshot(sandbox: SandboxProvider, workspace: Workspace, python: str) -> RepoSnapshot:
    result = await sandbox.run(
        workspace,
        f"{python} {COLLECTOR_FILE}",
        files={repo_path(COLLECTOR_FILE): COLLECTOR_SOURCE},
    )
    if not result.ok:
        raise RuntimeError(f"collecting the repository failed: {result.output()[-500:]}")
    data = json.loads(result.stdout)
    return RepoSnapshot(data["tree"], data["files"])


async def build_psm(
    llm: LLMClient, repo: RepoSnapshot, role: str, party: str, scope: str | None
) -> Psm:
    response = await llm.complete(
        LLMRequest(
            role=Role.PSM,
            messages=[
                {"role": "system", "content": PSM_BUILDER},
                {"role": "user", "content": repo.as_prompt(role, scope)},
            ],
            response_schema=Psm.model_json_schema(),
            party=party,
        )
    )
    return Psm.model_validate(response.parsed)
