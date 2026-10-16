import type { BrokerEvent, PartyView, Role, SessionState, SessionView } from "../api/types";
import type { ContractScenario } from "./contract";

export type Tone = "a" | "b" | "o" | "e" | "w" | "i" | "n";

const STATES: Record<SessionState, [string, Tone]> = {
  CREATED: ["Created", "n"],
  BRIEFING: ["Briefing", "i"],
  NEGOTIATING: ["Negotiating", "i"],
  ESCALATED: ["Escalated", "w"],
  AWAITING_SIGNATURES: ["Awaiting signatures", "w"],
  IMPLEMENTING: ["Implementing", "i"],
  AMENDING: ["Amending contract", "w"],
  E2E: ["End-to-end", "i"],
  CERTIFIED: ["Certified", "o"],
  FAILED: ["Failed", "e"],
};

export const LIFECYCLE: SessionState[] = [
  "BRIEFING",
  "NEGOTIATING",
  "AWAITING_SIGNATURES",
  "IMPLEMENTING",
  "E2E",
  "CERTIFIED",
];

export function stateMeta(state: SessionState): { label: string; tone: Tone; active: boolean } {
  const [label, tone] = STATES[state] ?? [state, "n"];
  return { label, tone, active: tone === "i" };
}

export function roleTone(role: Role | undefined): Tone {
  return role === "provider" ? "b" : "a";
}

export function payload<T>(event: BrokerEvent): T {
  return event.payload as T;
}

export function ofType(events: BrokerEvent[], ...types: string[]): BrokerEvent[] {
  return events.filter((event) => types.includes(event.type));
}

export function partyOf(session: SessionView | null, key: string | null | undefined) {
  return session?.parties.find((party) => party.party === key);
}

export function partyName(session: SessionView | null, key: string | null | undefined): string {
  return partyOf(session, key)?.display_name ?? key ?? "";
}

export interface Turn {
  seq: number;
  ts: string;
  party: string;
  round: number;
  message: string;
  status: string;
  version: number;
  patches: string[];
  questions: string[];
}

export function turns(events: BrokerEvent[]): Turn[] {
  return ofType(events, "turn.accepted").map((event) => {
    const data = payload<{
      party: string;
      round: number;
      message: string;
      status: string;
      contract_version: number;
      patch_summary?: string[];
      questions?: string[];
    }>(event);
    return {
      seq: event.seq,
      ts: event.ts,
      party: data.party,
      round: data.round,
      message: data.message,
      status: data.status,
      version: data.contract_version,
      patches: data.patch_summary ?? [],
      questions: data.questions ?? [],
    };
  });
}

export type ChatItem =
  { kind: "turn"; turn: Turn } | { kind: "system"; seq: number; text: string; tone: Tone };

export function chat(events: BrokerEvent[], session: SessionView | null): ChatItem[] {
  const name = (key: string) => partyName(session, key);
  const items: ChatItem[] = [];
  for (const event of events) {
    const data = event.payload as Record<string, unknown>;
    if (event.type === "turn.accepted") {
      const [turn] = turns([event]);
      items.push({ kind: "turn", turn });
    } else if (event.type === "session.created") {
      items.push({
        kind: "system",
        seq: event.seq,
        text: `Session created · round limit ${session?.max_rounds ?? "—"}`,
        tone: "n",
      });
    } else if (event.type === "question.answered") {
      items.push({
        kind: "system",
        seq: event.seq,
        text: `${String(data.qid)} answered by ${humanName(event.actor, session)} → decision ${String(data.decision_id ?? "")}`,
        tone: "w",
      });
    } else if (event.type === "contract.signed") {
      items.push({
        kind: "system",
        seq: event.seq,
        text: `Contract v${String(data.version)} signed by both companies · implementation starts in isolated sandboxes`,
        tone: "o",
      });
    } else if (event.type === "contract.gap") {
      items.push({
        kind: "system",
        seq: event.seq,
        text: `${name(String(data.party))} reported a contract gap · amendment started`,
        tone: "w",
      });
    } else if (event.type === "session.escalated") {
      items.push({
        kind: "system",
        seq: event.seq,
        text: "Round limit reached · escalated to humans",
        tone: "w",
      });
    } else if (event.type === "e2e.result") {
      items.push({
        kind: "system",
        seq: event.seq,
        text: data.passed
          ? "End-to-end run green through the gateway"
          : "End-to-end run failed · sanitized report shared with both sides",
        tone: data.passed ? "o" : "e",
      });
    } else if (event.type === "certificate.issued") {
      items.push({
        kind: "system",
        seq: event.seq,
        text: `Certificate issued for contract v${String(data.version)}`,
        tone: "o",
      });
    }
  }
  return items;
}

export function humanName(actor: string, session: SessionView | null): string {
  if (actor.startsWith("human:")) return `${partyName(session, actor.slice(6))} human`;
  return partyName(session, actor) || actor;
}

export type TimelineKind = "agent" | "human" | "test" | "egress" | "gateway" | "system";

export interface TimelineItem {
  seq: number;
  ts: string;
  kind: TimelineKind;
  text: string;
  tone: Tone;
}

export function timeline(events: BrokerEvent[], session: SessionView | null): TimelineItem[] {
  const name = (key: unknown) => partyName(session, String(key));
  const items: TimelineItem[] = [];
  for (const event of events) {
    const data = event.payload as Record<string, unknown>;
    const push = (kind: TimelineKind, text: string, tone: Tone) =>
      items.push({ seq: event.seq, ts: event.ts, kind, text, tone });
    switch (event.type) {
      case "session.created":
        push("system", "Session created", "n");
        break;
      case "session.state_changed":
        push(
          "system",
          `State ${stateMeta(data.from as SessionState).label} → ${stateMeta(data.to as SessionState).label}`,
          "n",
        );
        break;
      case "psm.built":
        push(
          "agent",
          `${name(data.party)} agent built its private system model · ${String(data.files)} files`,
          "a",
        );
        break;
      case "brief.submitted":
        push("agent", `${name(data.party)} shared its brief`, "a");
        break;
      case "turn.accepted":
        push(
          "agent",
          `Round ${String(data.round)} · ${name(data.party)} → contract v${String(data.contract_version)} (${String(data.status)})`,
          "a",
        );
        break;
      case "turn.rejected":
        push(
          "agent",
          `Round ${String(data.round)} · ${name(data.party)} turn rejected by the broker`,
          "e",
        );
        break;
      case "question.created":
        push(
          "human",
          `${String(data.qid)} asked to ${name(data.party)} humans: ${String(data.text)}`,
          "w",
        );
        break;
      case "question.answered":
        push("human", `${String(data.qid)} answered: ${String(data.answer)}`, "w");
        break;
      case "signature.added":
        push("human", `${name(data.party)} signed contract v${String(data.version)}`, "w");
        break;
      case "contract.signed":
        push("system", `Contract v${String(data.version)} signed`, "o");
        break;
      case "contract.gap":
        push("agent", `${name(data.party)} reported a contract gap`, "w");
        break;
      case "tool.lookup_standard":
        push(
          "agent",
          `${name(data.party)} agent checked a public standard via Tavily: ${String(data.query)}`,
          "i",
        );
        break;
      case "dry_run.result":
        push(
          "test",
          Number(data.mismatches)
            ? `${name(data.party)} ran the draft contract on its real system · ${String(data.mismatches)} mismatches sent back to negotiation`
            : `${name(data.party)} ran the draft contract on its real system · no mismatches`,
          Number(data.mismatches) ? "w" : "o",
        );
        break;
      case "coder.progress":
        push(
          "agent",
          `${name(data.party)} coder · step ${String(data.step)} · ${String(data.note)}`,
          "a",
        );
        break;
      case "tests.result": {
        const results = (data.results as { status: string }[]) ?? [];
        const passed = results.filter((item) => item.status === "passed").length;
        push(
          "test",
          `${name(data.party)} contract tests ${passed}/${results.length} passing`,
          passed === results.length ? "o" : "e",
        );
        break;
      }
      case "e2e.started":
        push("test", "End-to-end run started through the gateway", "i");
        break;
      case "e2e.result":
        push(
          "test",
          data.passed ? "End-to-end run passed" : "End-to-end run failed",
          data.passed ? "o" : "e",
        );
        break;
      case "gateway.request":
        if (!data.allowed)
          push(
            "gateway",
            `Gateway rejected ${String(data.method)} ${String(data.path)} (403, not in contract)`,
            "e",
          );
        break;
      case "egress.blocked":
        push(
          "egress",
          `Egress guard blocked an outbound ${String(data.kind ?? "message")} from ${name(data.party)}`,
          "e",
        );
        break;
      case "egress.redacted":
        push(
          "egress",
          `Egress guard redacted an outbound ${String(data.kind ?? "message")} from ${name(data.party)}`,
          "w",
        );
        break;
      case "isolation.probe":
        push(
          "test",
          `Isolation probes ${data.ok ? "passed" : "found a problem"}`,
          data.ok ? "o" : "e",
        );
        break;
      case "certificate.issued":
        push("system", `Certificate issued for contract v${String(data.version)}`, "o");
        break;
      case "session.escalated":
        push("human", "Round limit reached · humans needed", "w");
        break;
    }
  }
  return items;
}

export type CellStatus = "pass" | "fail" | "running" | "idle" | "na";

export interface MatrixRow {
  id: string;
  title: string;
  side: ContractScenario["side"];
  cells: Record<"consumer" | "provider" | "e2e", CellStatus>;
}

function latestResults(events: BrokerEvent[], party: string | undefined): Map<string, string> {
  const runs = ofType(events, "tests.result").filter(
    (event) => payload<{ party: string }>(event).party === party,
  );
  const last = runs.at(-1);
  const results = last
    ? payload<{ results: { scenario_id: string; status: string }[] }>(last).results
    : [];
  return new Map(results.map((item) => [item.scenario_id, item.status]));
}

function latestE2E(events: BrokerEvent[]): Map<string, string> {
  const last = ofType(events, "e2e.result").at(-1);
  const scenarios = last
    ? (payload<{ report: { scenarios?: { id: string; status: string }[] } }>(last).report
        .scenarios ?? [])
    : [];
  return new Map(scenarios.map((item) => [item.id, item.status]));
}

const cell = (status: string | undefined, running: boolean): CellStatus =>
  status === "passed" ? "pass" : status ? "fail" : running ? "running" : "idle";

export function matrix(
  scenarios: ContractScenario[],
  events: BrokerEvent[],
  session: SessionView | null,
): MatrixRow[] {
  const consumer = session?.parties.find((party) => party.role === "consumer");
  const provider = session?.parties.find((party) => party.role === "provider");
  const consumerRuns = latestResults(events, consumer?.party);
  const providerRuns = latestResults(events, provider?.party);
  const e2e = latestE2E(events);
  const lastE2E = ofType(events, "e2e.started", "e2e.result").at(-1);
  const e2eRunning = session?.state === "E2E" && lastE2E?.type === "e2e.started";
  const coding = (party: PartyView | undefined) =>
    session?.state === "IMPLEMENTING" && party?.status === "implementing";
  const sideOrder = { consumer: 0, provider: 1, e2e: 2 };
  const ordered = [...scenarios].sort(
    (a, b) =>
      sideOrder[a.side] - sideOrder[b.side] ||
      a.id.localeCompare(b.id, undefined, { numeric: true }),
  );
  return ordered.map((scenario) => ({
    id: scenario.id,
    title: scenario.title,
    side: scenario.side,
    cells: {
      consumer:
        scenario.side === "consumer" ? cell(consumerRuns.get(scenario.id), coding(consumer)) : "na",
      provider:
        scenario.side === "provider" ? cell(providerRuns.get(scenario.id), coding(provider)) : "na",
      e2e: scenario.side === "e2e" ? cell(e2e.get(scenario.id), e2eRunning) : "na",
    },
  }));
}

export function costs(events: BrokerEvent[]) {
  const models = new Map<string, number>();
  const roles = new Map<string, number>();
  const sandboxes = new Map<string, number>();
  for (const event of ofType(events, "llm.call", "sandbox.cost")) {
    const data = payload<{ model?: string; role?: string; party: string; cost_usd: number }>(event);
    if (event.type === "llm.call") {
      models.set(data.model ?? "model", (models.get(data.model ?? "model") ?? 0) + data.cost_usd);
      const key = `${data.party} · ${data.role ?? "agent"}`;
      roles.set(key, (roles.get(key) ?? 0) + data.cost_usd);
    } else {
      sandboxes.set(data.party, (sandboxes.get(data.party) ?? 0) + data.cost_usd);
    }
  }
  return { models, roles, sandboxes };
}

export interface PsmStats {
  files: number;
  tokens: number;
  seconds: number;
}

export function psm(events: BrokerEvent[], party: string): PsmStats | null {
  const built = ofType(events, "psm.built")
    .filter((event) => payload<{ party: string }>(event).party === party)
    .at(-1);
  return built ? payload<PsmStats>(built) : null;
}

export interface CoderStep {
  seq: number;
  ts: string;
  step: number;
  files: number;
  note: string;
}

export function coderSteps(events: BrokerEvent[], party: string): CoderStep[] {
  return ofType(events, "coder.progress")
    .filter((event) => payload<{ party: string }>(event).party === party)
    .map((event) => {
      const data = payload<{ step: number; files_changed: number; note: string }>(event);
      return {
        seq: event.seq,
        ts: event.ts,
        step: data.step,
        files: data.files_changed,
        note: data.note,
      };
    });
}

export function brief(events: BrokerEvent[], party: string): Record<string, unknown> | null {
  const submitted = ofType(events, "brief.submitted")
    .filter((event) => payload<{ party: string }>(event).party === party)
    .at(-1);
  return submitted ? payload<{ brief: Record<string, unknown> }>(submitted).brief : null;
}

export function signedParties(events: BrokerEvent[], version: number): Set<string> {
  return new Set(
    ofType(events, "signature.added")
      .map((event) => payload<{ party: string; version: number }>(event))
      .filter((data) => data.version === version)
      .map((data) => data.party),
  );
}

export interface GatewayRequest {
  seq: number;
  ts: string;
  party: string;
  method: string;
  path: string;
  status: number;
  ms: number;
  allowed: boolean;
  source: string;
  violations: string[];
}

export function gatewayRequests(events: BrokerEvent[]): GatewayRequest[] {
  return ofType(events, "gateway.request").map((event) => {
    const data = payload<Partial<GatewayRequest>>(event);
    return {
      seq: event.seq,
      ts: event.ts,
      party: data.party ?? "",
      method: data.method ?? "",
      path: data.path ?? "",
      status: data.status ?? 0,
      ms: data.ms ?? 0,
      allowed: data.allowed ?? false,
      source: data.source ?? (data.allowed ? "contract" : "off-contract"),
      violations: data.violations ?? [],
    };
  });
}

export interface VersionInfo {
  version: number;
  ts: string;
  actor: string;
  added: number;
  removed: number;
}

export function contractVersions(events: BrokerEvent[]): VersionInfo[] {
  const created = events.find((event) => event.type === "session.created");
  const versions: VersionInfo[] = [
    { version: 1, ts: created?.ts ?? "", actor: "broker", added: 0, removed: 0 },
  ];
  for (const event of ofType(events, "contract.version_created")) {
    const data = payload<{
      version: number;
      diff_stats?: { lines_added: number; lines_removed: number };
    }>(event);
    versions.push({
      version: data.version,
      ts: event.ts,
      actor: event.actor,
      added: data.diff_stats?.lines_added ?? 0,
      removed: data.diff_stats?.lines_removed ?? 0,
    });
  }
  return versions;
}

export function elapsedMs(
  events: BrokerEvent[],
  state: SessionState | undefined,
  now: number,
): number {
  const first = events[0];
  if (!first) return 0;
  const finished = events.findLast(
    (event) =>
      event.type === "certificate.issued" ||
      (event.type === "session.state_changed" && event.payload.to === "FAILED"),
  );
  const end =
    state === "CERTIFIED" || state === "FAILED"
      ? new Date((finished ?? events.at(-1) ?? first).ts).getTime()
      : now;
  return end - new Date(first.ts).getTime();
}

export function probeResults(events: BrokerEvent[]) {
  const last = ofType(events, "isolation.probe").at(-1);
  return last
    ? {
        ts: last.ts,
        seq: last.seq,
        ...payload<{
          ok: boolean;
          results: { name: string; expected: string; actual: string; status: string }[];
        }>(last),
      }
    : null;
}
