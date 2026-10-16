import asyncio
import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from doubleblind_agent.brief import write_brief
from doubleblind_agent.broker_client import BrokerClient, BrokerRejectedError
from doubleblind_agent.coder import run_coder
from doubleblind_agent.dry_run import DryRunToolbox, contract_text, dry_run
from doubleblind_agent.egress_guard import EgressGuard
from doubleblind_agent.models import Psm
from doubleblind_agent.negotiator import negotiation_context, patched, propose_turn
from doubleblind_agent.openapi import SPEC_FILES, contract_sections
from doubleblind_agent.psm import build_psm, snapshot
from doubleblind_agent.standards import lookup_standard
from doubleblind_agent.tools import BINDING_FILE, Toolbox
from doubleblind_agent.triage import triage_e2e
from doubleblind_contract.guard_rules import SyntheticAllowlist
from doubleblind_contract.loader import parse_yaml
from doubleblind_llm import LLMClient
from doubleblind_llm.domain import LLMCallEvent
from doubleblind_sandbox.environment import recorded_bases, remote_sandbox
from doubleblind_sandbox.provider import ServiceSandbox, Workspace

SandboxFactory = Callable[[], ServiceSandbox]
MAX_CONTINUATIONS = 2
MAX_DRY_RUNS = 3
ACTIONS = frozenset({"build_psm_and_brief", "negotiate", "amend", "implement", "fix_after_e2e"})


UNDELIVERED_WEBHOOKS = (
    "No webhook reached the consumer during the end-to-end test, so steps that wait for one fail. "
    "The provider's setup must register the consumer's webhook receiver at {{capture.url}} before "
    "each e2e scenario (a do step calling a binding action), so the provider delivers its webhooks "
    "there."
)


def undelivered(feedback: dict[str, Any], contract: dict[str, Any]) -> list[str]:
    delivered = any(
        str(item.get("source", "")).startswith("webhook ") for item in feedback["gateway"]
    )
    if delivered or not contract.get("webhooks"):
        return []
    return [item["id"] for item in feedback["report"]["scenarios"] if item["status"] != "passed"]


def provider_part(contract: dict[str, Any]) -> str:
    scenarios = [item for item in contract.get("scenarios") or [] if item.get("side") == "provider"]
    sections = [contract.get(key) for key in ("operations", "webhooks", "schemas")]
    setup = (contract.get("setup") or {}).get("provider")
    return json.dumps([*sections, setup, scenarios], sort_keys=True)


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
        party = os.environ["PARTY"]
        tag = os.environ.get("SANDBOX_BASE_TAG")
        recorded = recorded_bases(os.environ.get("DOUBLEBLIND_SANDBOX", "tokenfactory")) or {}
        base = f"tag:{tag}" if tag else recorded.get(party)
        if base is None:
            raise SystemExit("set SANDBOX_BASE_TAG or build the base images first")
        return cls(
            os.environ["BROKER_URL"],
            os.environ["AGENT_TOKEN"],
            os.environ["SESSION_ID"],
            party,
            base,
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
        self.idle = False
        self.continuations = 0
        self.previous = ""
        self.standards: list[dict[str, Any]] = []
        self.dry_runs = 0
        self.dry_binding: str | None = None
        self.seeded: str | None = None
        self.checked = ""
        self.gap: dict[str, Any] | None = None

    def save(self, name: str, value: Any) -> None:
        self.config.state_dir.mkdir(parents=True, exist_ok=True)
        path = self.config.state_dir / name
        path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

    def load(self, name: str) -> Any:
        return json.loads((self.config.state_dir / name).read_text(encoding="utf-8"))

    def api(self) -> dict[str, Any] | None:
        path = self.config.state_dir / "api.json"
        found: dict[str, Any] | None = self.load("api.json") if path.is_file() else None
        return found

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

    async def report_sandbox_cost(self, broker: BrokerClient, sandbox: ServiceSandbox) -> None:
        if sandbox.spent_usd:
            await broker.post_event({"type": "sandbox.cost", "cost_usd": sandbox.spent_usd})

    async def build_private_model(self, broker: BrokerClient, context: dict[str, Any]) -> Psm:
        party, role = self.config.party, context["role"]
        started = time.monotonic()
        sandbox = self.sandbox()
        try:
            workspace = await sandbox.open_workspace(party, self.config.base)
            repo = await snapshot(sandbox, workspace, sandbox.python)
        finally:
            await sandbox.aclose()
        await self.report_sandbox_cost(broker, sandbox)
        psm = await build_psm(self.llm, repo, role, party)
        self.save("psm.json", psm.model_dump())
        spec = next((item for item in repo.files if item["path"] in SPEC_FILES), None)
        self.save("api.json", contract_sections(parse_yaml(spec["content"])) if spec else None)
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
        return psm

    async def private_model(self, broker: BrokerClient, context: dict[str, Any]) -> Psm:
        if (self.config.state_dir / "psm.json").is_file():
            return Psm.model_validate(self.load("psm.json"))
        return await self.build_private_model(broker, context)

    async def build_psm_and_brief(self, broker: BrokerClient, context: dict[str, Any]) -> None:
        party, role = self.config.party, context["role"]
        psm = await self.build_private_model(broker, context)

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

        api = self.api() if role == "provider" else None
        fresh_gap = context.get("gap") != self.gap
        if fresh_gap:
            self.gap, self.dry_runs = context.get("gap"), 0

        async def turn(feedback: list[str]) -> dict[str, Any]:
            situation = {
                **await negotiation_context(broker),
                "gap": context.get("gap"),
                "standards": list(self.standards),
                "api_from_spec": bool(api),
            }
            from_spec = []
            if api and not situation["contract"].get("operations"):
                from_spec = [{"op": "add", "path": f"/{key}", "value": api[key]} for key in api]
                situation["contract"] = patched(situation["contract"], from_spec)
                situation["contract_yaml"] = contract_text(situation["contract"])
            proposed = await propose_turn(
                self.llm, psm, party, role, situation, synthetic, vet, feedback
            )
            await self.look_up(broker, guard, proposed.pop("lookup_standards"))
            if proposed["patches"]:
                proposed["patches"] = [*from_spec, *proposed["patches"]]
            candidate = patched(situation["contract"], proposed["patches"])
            checkable = bool(candidate.get("operations")) and any(
                item.get("side") == "provider" for item in candidate.get("scenarios") or []
            )
            if (
                role == "provider"
                and checkable
                and provider_part(candidate) != self.checked
                and self.dry_runs < MAX_DRY_RUNS
            ):
                self.dry_runs += 1
                checked = await self.check_on_real_system(broker, candidate, psm)
                self.checked = provider_part(patched(candidate, checked))
                if checked:
                    proposed["patches"] = [*proposed["patches"], *checked]
                    proposed["message"] += (
                        "\n(We ran our part of the contract on our real system and corrected it.)"
                    )
            self.log.append(f"round {proposed['round']}: {proposed['status']}")
            return proposed

        await guard.send("turns", turn, broker.post_turn)

    async def check_on_real_system(
        self, broker: BrokerClient, contract: dict[str, Any], psm: Psm
    ) -> list[dict[str, Any]]:
        sandbox = self.sandbox()
        try:
            workspace = await sandbox.open_workspace(self.config.party, self.config.base)
            secrets = {
                item["name"]: f"dry-run-{item['name']}" for item in contract.get("secrets", [])
            }
            toolbox = DryRunToolbox(
                sandbox,
                workspace,
                self.llm,
                self.config.party,
                "provider",
                contract_text(contract),
                secrets,
                "doubleblind/dry-run",
                locked=self.api() is not None,
            )
            await toolbox.prepare()
            patches, self.dry_binding, removed = await dry_run(
                self.llm, toolbox, psm, self.dry_binding
            )
        finally:
            await sandbox.aclose()
        await self.report_sandbox_cost(broker, sandbox)
        await broker.post_event({"type": "dry_run.result", "mismatches": removed})
        self.log.append(f"dry run: {len(patches) - removed} fixes, {removed} failing removed")
        return patches

    async def look_up(self, broker: BrokerClient, guard: EgressGuard, queries: list[str]) -> None:
        known = {item["query"] for item in self.standards}
        for query in queries:
            if query in known or guard.check("lookup", {"query": query}).blocked:
                continue
            found = await lookup_standard(query)
            known.add(query)
            if found is not None:
                self.standards.append(found)
                await broker.post_event({"type": "tool.lookup_standard", **found})

    async def workspace(self, sandbox: ServiceSandbox) -> Workspace:
        path = self.config.state_dir / "workspace.json"
        if path.is_file():
            saved = self.load("workspace.json")
            return Workspace(self.config.party, saved["head"], saved["history"])
        return await sandbox.open_workspace(self.config.party, self.config.base)

    async def implement(self, broker: BrokerClient, context: dict[str, Any]) -> bool:
        party, side = self.config.party, context["role"]
        self.gap = None
        contract_yaml = await broker.get(f"/contract?version={context['signed_version']}")
        psm = await self.private_model(broker, context)
        sandbox = self.sandbox()

        async def progress(step: int, files_changed: int, note: str) -> None:
            event = {"type": "coder.progress", "step": step, "files_changed": files_changed}
            await broker.post_event({**event, "note": note})

        try:
            workspace = await self.workspace(sandbox)
            toolbox = Toolbox(
                sandbox,
                workspace,
                self.llm,
                party,
                side,
                contract_yaml,
                context["secrets"],
                f"doubleblind/{self.config.session_id}",
            )
            await toolbox.prepare()
            previous = self.previous
            fresh = self.dry_binding != self.seeded and not context.get("sanitized_e2e")
            if self.dry_binding and fresh and not previous:
                self.seeded = self.dry_binding
                await toolbox.write(BINDING_FILE, self.dry_binding)
                previous = (
                    f"{BINDING_FILE} holds the binding you wrote when you checked the draft "
                    "contract on the real system; run contract_tests first and fix what fails."
                )
            outcome = await run_coder(
                self.llm,
                toolbox,
                psm,
                contract_yaml,
                context.get("sanitized_e2e"),
                previous,
                progress,
            )
            self.save("workspace.json", {"head": workspace.head, "history": workspace.history})
            binding = await toolbox.read_text(BINDING_FILE) if outcome.kind == "done" else ""
            if outcome.kind == "done":
                diff = await toolbox.shell(f"git diff --stat {toolbox.base_commit} HEAD")
                await sandbox.tag(workspace, f"doubleblind-{party}-{self.config.session_id}")
        finally:
            await sandbox.aclose()
        await self.report_sandbox_cost(broker, sandbox)
        self.save("coder_transcript.json", outcome.transcript)
        self.log.append(f"coder: {outcome.kind} ({outcome.summary[:200]})")
        if outcome.kind == "contract_gap":
            await self.report_gap(
                broker, {"scenario_ids": outcome.scenario_ids, "description": outcome.summary}
            )
            return True
        if outcome.kind == "stuck":
            scenarios = (outcome.report or {}).get("scenarios", [])
            statuses = [f"{item['id']} {item['status']}" for item in scenarios]
            self.previous = "\n".join(
                [*statuses, outcome.summary] if statuses else ["contract tests were not run yet"]
            )
            self.continuations += 1
            return self.continuations <= MAX_CONTINUATIONS
        if outcome.report is None:
            return False
        (self.config.state_dir / "PR.md").write_text(
            f"# DoubleBlind: {party}\n\n{outcome.summary}\n\n```\n{diff.stdout}\n```\n",
            encoding="utf-8",
        )
        run = {
            "side": side,
            "contract_version": context["signed_version"],
            "summary": outcome.report["summary"],
            "results": [
                {"scenario_id": item["id"], "status": item["status"]}
                for item in outcome.report["scenarios"]
            ],
            "binding_yaml": binding,
            "workspace_image": workspace.head,
        }
        self.save("test_run.json", run)
        await self.send_results(broker)
        return True

    async def send_results(self, broker: BrokerClient) -> None:
        run = self.load("test_run.json")

        async def results(feedback: list[str]) -> dict[str, Any]:
            return dict(run)

        await self.guard(broker).send("results", results, broker.post_test_run)

    async def report_gap(self, broker: BrokerClient, gap: dict[str, Any]) -> None:
        async def reported_gap(feedback: list[str]) -> dict[str, Any]:
            return gap

        await self.guard(broker).send("results", reported_gap, broker.post_contract_gap)

    async def fix_after_e2e(self, broker: BrokerClient, context: dict[str, Any]) -> bool:
        feedback = context["sanitized_e2e"]
        contract = parse_yaml(await broker.get(f"/contract?version={context['signed_version']}"))
        silent = undelivered(feedback, contract) if context["role"] == "provider" else []
        if silent:
            self.log.append(f"e2e: no webhook delivered, contract gap in {', '.join(silent)}")
            await self.report_gap(
                broker, {"scenario_ids": silent, "description": UNDELIVERED_WEBHOOKS}
            )
            return True
        verdict = await triage_e2e(self.llm, feedback, self.config.party, context["role"])
        self.log.append(f"e2e triage: {verdict.category} ({verdict.sanitized_reason_for_broker})")
        if verdict.category == "CONTRACT_GAP":
            await self.report_gap(
                broker,
                {
                    "scenario_ids": verdict.scenario_ids,
                    "description": verdict.sanitized_reason_for_broker,
                },
            )
            return True
        if verdict.category != "CODE_BUG":
            await self.send_results(broker)
            return True
        found = {**feedback, "our_triage": verdict.summary_for_coder}
        return await self.implement(broker, {**context, "sanitized_e2e": found})

    async def act(self, broker: BrokerClient, name: str, context: dict[str, Any]) -> bool:
        if name == "build_psm_and_brief":
            await self.build_psm_and_brief(broker, context)
            return True
        if name in ("negotiate", "amend"):
            await self.negotiate(broker, context)
            return True
        if name == "fix_after_e2e":
            return await self.fix_after_e2e(broker, context)
        return await self.implement(broker, context)

    async def run(self) -> None:
        broker = BrokerClient(self.config.broker_url, self.config.token, self.config.session_id)
        try:
            while True:
                action = await broker.next_action()
                name = action["action"]
                self.idle = name == "wait"
                if self.idle:
                    continue
                if name not in self.actions:
                    self.log.append(f"stopped at action {name}")
                    return
                try:
                    carry_on = await self.act(broker, name, action["context"])
                except BrokerRejectedError as error:
                    if error.code != "WRONG_STATE":
                        raise
                    self.log.append(f"{name}: the session moved on ({error})")
                    carry_on = True
                await self.report_llm_calls(broker)
                if not carry_on:
                    return
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
