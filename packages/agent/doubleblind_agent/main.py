import asyncio
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doubleblind_agent.brief import write_brief
from doubleblind_agent.broker_client import BrokerClient
from doubleblind_agent.egress_guard import EgressGuard
from doubleblind_agent.models import Psm
from doubleblind_agent.negotiator import negotiation_context, propose_turn
from doubleblind_agent.psm import build_psm, snapshot
from doubleblind_contract.guard_rules import SyntheticAllowlist
from doubleblind_llm import LLMClient
from doubleblind_llm.domain import LLMCallEvent
from doubleblind_sandbox.environment import remote_sandbox
from doubleblind_sandbox.provider import ServiceSandbox

SandboxFactory = Callable[[], ServiceSandbox]
ACTIONS = frozenset({"build_psm_and_brief", "negotiate", "amend"})


@dataclass(frozen=True)
class PartyConfig:
    broker_url: str
    token: str
    session_id: str
    party: str
    base: str
    state_dir: Path

    @classmethod
    def from_env(cls) -> "PartyConfig":
        return cls(
            os.environ["BROKER_URL"],
            os.environ["AGENT_TOKEN"],
            os.environ["SESSION_ID"],
            os.environ["PARTY"],
            f"tag:{os.environ['SANDBOX_BASE_TAG']}",
            Path(os.environ.get("DOUBLEBLIND_STATE_DIR", "/state/.doubleblind")),
        )


class PartyRuntime:
    def __init__(
        self, config: PartyConfig, sandbox: SandboxFactory, actions: frozenset[str] = ACTIONS
    ) -> None:
        self.config = config
        self.sandbox = sandbox
        self.actions = actions
        self.calls: list[LLMCallEvent] = []
        self.llm = LLMClient.from_env(on_call=self.calls.append)
        self.log: list[str] = []

    def save(self, name: str, value: Any) -> None:
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.state_dir / name
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, name: str) -> Any:
        return json.loads((self.config.state_dir / name).read_text(encoding="utf-8"))

    def guard(self, broker: BrokerClient) -> EgressGuard:
        egress = self.load("egress.json")
        allowlist = SyntheticAllowlist.build(**egress["synthetic_allowlist"])
        return EgressGuard(self.llm, broker, allowlist, egress["repo_paths"], self.config.party)

    async def report_llm_calls(self, broker: BrokerClient) -> None:
        while self.calls:
            call = self.calls.pop(0)
            await broker.post_event(
                {
                    "type": "llm.call",
                    "role": call.role,
                    "model": call.model,
                    "tokens": call.prompt_tokens + call.completion_tokens,
                    "cost_usd": call.cost_usd,
                    "latency_ms": call.latency_ms,
                }
            )

    async def build_psm_and_brief(self, broker: BrokerClient, context: dict[str, Any]) -> None:
        party, role = self.config.party, context["role"]
        started = time.monotonic()
        sandbox = self.sandbox()
        try:
            workspace = await sandbox.open_workspace(party, self.config.base)
            repo = await snapshot(sandbox, workspace, sandbox.python)
        finally:
            await sandbox.aclose()
        psm = await build_psm(self.llm, repo, role, party)
        self.save("psm.json", psm.model_dump())
        await broker.post_event(
            {
                "type": "psm.built",
                "files": len(repo.files),
                "tokens": repo.tokens,
                "seconds": round(time.monotonic() - started, 1),
            }
        )
        self.log.append(f"psm: {len(repo.files)} files, {repo.tokens} tokens")
        self.save(
            "egress.json",
            {"synthetic_allowlist": context["synthetic_allowlist"], "repo_paths": repo.tree},
        )

        async def brief(feedback: list[str]) -> dict[str, Any]:
            synthetic = context["synthetic_allowlist"]
            written = (
                await write_brief(self.llm, psm, party, role, synthetic, feedback)
            ).model_dump()
            self.save("brief.json", written)
            return written

        await self.guard(broker).send("brief", brief, broker.post_brief)
        self.log.append("brief sent")

    async def negotiate(self, broker: BrokerClient, context: dict[str, Any]) -> None:
        party, role = self.config.party, context["role"]
        psm = Psm.model_validate(self.load("psm.json"))
        synthetic = self.load("egress.json")["synthetic_allowlist"]
        guard = self.guard(broker)

        def vet(payload: dict[str, Any]) -> list[str]:
            return guard.check("turns", payload).reasons

        async def turn(feedback: list[str]) -> dict[str, Any]:
            situation = await negotiation_context(broker)
            proposed = await propose_turn(
                self.llm, psm, party, role, situation, synthetic, vet, feedback
            )
            self.log.append(f"round {proposed['round']}: {proposed['status']}")
            return proposed

        await guard.send("turns", turn, broker.post_turn)

    async def run(self) -> None:
        broker = BrokerClient(self.config.broker_url, self.config.token, self.config.session_id)
        try:
            while True:
                action = await broker.next_action()
                name = action["action"]
                if name == "wait":
                    continue
                if name not in self.actions:
                    self.log.append(f"stopped at action {name}")
                    return
                if name == "build_psm_and_brief":
                    await self.build_psm_and_brief(broker, action["context"])
                else:
                    await self.negotiate(broker, action["context"])
                await self.report_llm_calls(broker)
        finally:
            await broker.aclose()


def main() -> None:
    runtime = PartyRuntime(
        PartyConfig.from_env(),
        remote_sandbox(os.environ.get("DOUBLEBLIND_SANDBOX", "tokenfactory")),
    )
    asyncio.run(runtime.run())
    for line in runtime.log:
        print(f"[{runtime.config.party}] {line}")


if __name__ == "__main__":
    main()
