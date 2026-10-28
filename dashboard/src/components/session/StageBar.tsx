import { Fragment, useState } from "react";
import { api, errorText } from "../../api/client";
import type { BrokerEvent, Question, SessionState, SessionView } from "../../api/types";
import { duration } from "../../lib/format";
import { coderSteps, ofType, payload, signedParties } from "../../lib/model";
import { OBSERVER, useSessionContext } from "../../state/session";

const EXTRA_ROUNDS = 6;

const STAGES: [string, SessionState[]][] = [
  ["Private models and briefs", ["CREATED", "BRIEFING"]],
  ["Contract negotiation", ["NEGOTIATING", "ESCALATED", "AMENDING"]],
  ["Signatures", ["AWAITING_SIGNATURES"]],
  ["Implementation", ["IMPLEMENTING"]],
  ["End-to-end test", ["E2E"]],
  ["Certified", ["CERTIFIED"]],
];

function stageOf(state: SessionState): number {
  return STAGES.findIndex(([, states]) => states.includes(state));
}

function failedFrom(events: BrokerEvent[]): SessionState {
  const failed = ofType(events, "session.state_changed")
    .map((event) => payload<{ from: SessionState; to: SessionState }>(event))
    .findLast((change) => change.to === "FAILED");
  return failed?.from ?? "CREATED";
}

function nowText(session: SessionView, events: BrokerEvent[], questions: Question[]): string {
  const name = (party: string | null) =>
    session.parties.find((item) => item.party === party)?.display_name ?? "the partner";
  const open = questions.filter((question) => !question.answer);
  switch (session.state) {
    case "CREATED":
      return "Waiting for the run to start.";
    case "BRIEFING":
      return "Each agent reads only its own company's code and writes a brief for the partner.";
    case "NEGOTIATING":
    case "AMENDING": {
      const amending = session.state === "AMENDING" ? "Amending the contract after a gap · " : "";
      if (open.length)
        return `${amending}Waiting for ${name(open[0].party)} to answer ${open
          .map((question) => question.id)
          .join(", ")}.`;
      return `${amending}Round ${session.round + 1}: ${name(session.turn_party)} is drafting a contract change.`;
    }
    case "ESCALATED":
      return "The round limit was reached: the humans decide whether to continue.";
    case "AWAITING_SIGNATURES": {
      const signed = signedParties(events, session.current_contract_version);
      const waiting = session.parties.filter((party) => !signed.has(party.party));
      return `Contract v${session.current_contract_version} is ready: waiting for ${waiting
        .map((party) => party.display_name)
        .join(" and ")} to sign.`;
    }
    case "IMPLEMENTING":
      return `Each agent builds its own side in its own sandbox: ${session.parties
        .map((party) => {
          if (party.status === "ready_for_e2e") return `${party.display_name} contract tests green`;
          const step = coderSteps(events, party.party).at(-1);
          return `${party.display_name} ${step ? `at step ${step.step}` : "implementing"}`;
        })
        .join(" · ")}.`;
    case "E2E": {
      const started = ofType(events, "e2e.started").at(-1)?.seq ?? 0;
      const requests = ofType(events, "gateway.request").filter((event) => event.seq > started);
      return `Both real apps answer the end-to-end scenarios through the gateway: ${requests.length} requests so far.`;
    }
    case "CERTIFIED":
      return "Certified: every scenario is green on both sides and end to end.";
    case "FAILED":
      return ofType(events, "session.cancelled").length ? "Stopped by a human." : "The run failed.";
  }
}

export function StageBar() {
  const { id, token, view, data, now, canAct } = useSessionContext();
  const [error, setError] = useState<string | null>(null);
  const session = data.session;
  if (!session) return null;
  const failed = session.state === "FAILED";
  const certified = session.state === "CERTIFIED";
  const escalated = session.state === "ESCALATED";
  const current = stageOf(failed ? failedFrom(data.events) : session.state);
  const since = ofType(data.events, "session.state_changed").at(-1)?.ts;
  const integration = session.integration_id;
  const act = (action: () => Promise<unknown>) => {
    setError(null);
    action().catch((failure: unknown) => setError(errorText(failure)));
  };
  return (
    <div className="stagebar" data-testid="stage-bar">
      <div className="stages">
        {STAGES.map(([label], index) => {
          const done = index < current || certified;
          const status = done
            ? "done"
            : index === current
              ? failed
                ? "failed"
                : escalated
                  ? "now wait"
                  : "now"
              : "";
          return (
            <Fragment key={label}>
              {index > 0 && <span className="stage-arrow">→</span>}
              <span className={`stage ${status}`}>
                <span className="mark">{done ? "✓" : index + 1}</span>
                {label}
              </span>
            </Fragment>
          );
        })}
      </div>
      <div className="stage-now">
        <span
          className="dot"
          style={{
            background: escalated
              ? "var(--w)"
              : certified
                ? "var(--o)"
                : failed
                  ? "var(--e)"
                  : "var(--p)",
          }}
        />
        <span>
          {nowText(session, data.events, data.questions)}
          {since && !failed && !certified && ` · ${duration(now - Date.parse(since))}`}
        </span>
        {escalated && view !== OBSERVER && canAct && (
          <>
            <button
              type="button"
              className="btn btn-sm btn-primary"
              onClick={() => act(() => api.resume(id, token, EXTRA_ROUNDS))}
            >
              Allow {EXTRA_ROUNDS} more rounds
            </button>
            {integration && (
              <button
                type="button"
                className="btn btn-sm"
                onClick={() => act(() => api.stopLive(integration))}
              >
                Stop run
              </button>
            )}
          </>
        )}
      </div>
      {error && <div className="alert alert-error">{error}</div>}
    </div>
  );
}
