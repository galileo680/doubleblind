import type { BrokerEvent, Metrics, Question, SessionState, SessionView } from "../api/types";
import { ofType, payload } from "./model";

const QUICK = new Set([
  "metrics.update",
  "llm.call",
  "sandbox.cost",
  "coder.progress",
  "gateway.request",
  "party.status",
]);
export const REPLAY_SPEEDS = { slow: 900, normal: 450, fast: 120 } as const;
export type ReplaySpeed = keyof typeof REPLAY_SPEEDS;

export function stepDelay(event: BrokerEvent | undefined, speed: ReplaySpeed): number {
  const base = REPLAY_SPEEDS[speed];
  if (!event) return base;
  return QUICK.has(event.type) ? Math.max(20, base / 8) : base;
}

function lastPayload<T>(events: BrokerEvent[], type: string): T | undefined {
  const event = ofType(events, type).at(-1);
  return event ? payload<T>(event) : undefined;
}

export function replaySession(current: SessionView, events: BrokerEvent[]): SessionView {
  const state = lastPayload<{ to: SessionState }>(events, "session.state_changed")?.to ?? "CREATED";
  const lastTurn = lastPayload<{ round: number; party: string }>(events, "turn.accepted");
  const other = current.parties.find((party) => party.party !== lastTurn?.party);
  const asked = ofType(events, "question.created").length;
  const answered = ofType(events, "question.answered").length;
  const briefs = new Set(
    ofType(events, "brief.submitted").map((event) => payload<{ party: string }>(event).party),
  );
  const metrics = lastPayload<Metrics>(events, "metrics.update");
  const statuses = new Map(
    ofType(events, "party.status").map((event) => {
      const data = payload<{ party: string; status: string }>(event);
      return [data.party, data.status];
    }),
  );
  return {
    ...current,
    state,
    round: lastTurn?.round ?? 0,
    turn_party: lastTurn
      ? (other?.party ?? null)
      : (current.parties.find((party) => party.role === "provider")?.party ?? null),
    current_contract_version:
      lastPayload<{ version: number }>(events, "contract.version_created")?.version ?? 1,
    signed_contract_version:
      lastPayload<{ version: number }>(events, "contract.signed")?.version ?? null,
    open_questions: asked - answered,
    parties: current.parties.map((party) => ({
      ...party,
      status: statuses.get(party.party) ?? "waiting",
      brief_submitted: briefs.has(party.party),
    })),
    metrics: metrics
      ? { ...current.metrics, ...metrics }
      : {
          ...current.metrics,
          cost_usd: 0,
          sandbox_cost_usd: 0,
          tokens: 0,
          code_lines_shared: 0,
          blocked_count: 0,
          redacted_count: 0,
          bytes_brief: 0,
          bytes_turns: 0,
          bytes_results: 0,
        },
  };
}

export function replayQuestions(events: BrokerEvent[]): Question[] {
  const answers = new Map(
    ofType(events, "question.answered").map((event) => {
      const data = payload<{ qid: string; answer: string; decision_id: string | null }>(event);
      return [data.qid, { ...data, by: event.actor }];
    }),
  );
  return ofType(events, "question.created").map((event) => {
    const data = payload<{
      qid: string;
      party: string;
      text: string;
      options: string[];
      default?: string | null;
    }>(event);
    const answer = answers.get(data.qid);
    return {
      id: data.qid,
      party: data.party,
      text: data.text,
      options: data.options ?? [],
      default: data.default ?? null,
      answer: answer?.answer ?? null,
      answered_by: answer?.by ?? null,
      decision_id: answer?.decision_id ?? null,
    };
  });
}
