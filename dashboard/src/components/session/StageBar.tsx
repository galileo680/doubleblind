import { Fragment } from "react";
import type { BrokerEvent, Question, SessionState, SessionView } from "../../api/types";
import { duration } from "../../lib/format";
import { coderSteps, ofType, payload, signedParties } from "../../lib/model";
import { useSessionContext } from "../../state/session";

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
  const { data, now } = useSessionContext();
  const session = data.session;
  if (!session) return null;
  const failed = session.state === "FAILED";
  const current = stageOf(failed ? failedFrom(data.events) : session.state);
  const since = ofType(data.events, "session.state_changed").at(-1)?.ts;
  const finished = failed || session.state === "CERTIFIED";
  return (
    <div className="stagebar" data-testid="stage-bar">
      <div className="stages">
        {STAGES.map(([label], index) => {
          const status =
            index < current || (session.state === "CERTIFIED" && index === current)
              ? "done"
              : index === current
                ? failed
                  ? "failed"
                  : "now"
                : "";
          return (
            <Fragment key={label}>
              {index > 0 && (
                <span className={`stage-arrow${index <= current ? " passed" : ""}`}>→</span>
              )}
              <span className={`stage ${status}`}>{label}</span>
            </Fragment>
          );
        })}
      </div>
      <div className="stage-now">
        {nowText(session, data.events, data.questions)}
        {since && !finished && (
          <span className="muted"> · this stage for {duration(now - Date.parse(since))}</span>
        )}
      </div>
    </div>
  );
}
