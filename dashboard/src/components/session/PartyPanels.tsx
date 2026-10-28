import { useState } from "react";
import { api } from "../../api/client";
import type { PartyView, Question, SessionView } from "../../api/types";
import {
  brief,
  coderSteps,
  ofType,
  partyName,
  payload,
  psm,
  roleTone,
  signedParties,
  turns,
} from "../../lib/model";
import { OBSERVER, useSessionContext } from "../../state/session";
import { LockedTile, Swatch } from "../ui";

const BRIEF_LABELS: Record<string, string> = {
  data_we_have: "Data we have",
  capabilities_offered: "We offer",
  constraints: "Constraints",
  edge_cases: "Edge cases",
  open_needs: "Open needs",
  needs: "Needs",
};

function nowText(
  party: PartyView,
  session: SessionView,
  questions: Question[],
  lastNote: string | undefined,
  hasPsm: boolean,
): string {
  const other = session.parties.find((item) => item.party !== party.party);
  const open = questions.filter((question) => question.party === party.party && !question.answer);
  switch (session.state) {
    case "CREATED":
      return "Waiting for the session to start";
    case "BRIEFING":
      if (party.brief_submitted) return "Brief shared · waiting for the partner";
      return hasPsm
        ? "Writing the brief from its system model"
        : "Building its private system model";
    case "NEGOTIATING":
    case "AMENDING":
      if (open.length)
        return `Waiting for a human answer to ${open.map((question) => question.id).join(", ")}`;
      if (session.turn_party === party.party) return `Drafting round ${session.round + 1}`;
      return `Waiting for ${other?.display_name ?? "the partner"}`;
    case "ESCALATED":
      return "Round limit reached · humans needed";
    case "AWAITING_SIGNATURES":
      return `Contract v${session.current_contract_version} ready · waiting for signatures`;
    case "IMPLEMENTING":
      if (party.status === "ready_for_e2e") return "Contract tests green · waiting for the partner";
      return lastNote ? `Implementing · ${lastNote}` : "Implementing in its isolated sandbox";
    case "E2E":
      return "Serving end-to-end requests through the gateway";
    case "CERTIFIED":
      return "Done · integration certified";
    case "FAILED":
      return "Stopped · session failed";
  }
}

function AgentCard({ party, own }: { party: PartyView; own: boolean }) {
  const { data } = useSessionContext();
  const session = data.session;
  if (!session) return null;
  const steps = coderSteps(data.events, party.party);
  const now = nowText(
    party,
    session,
    data.questions,
    steps.at(-1)?.note,
    psm(data.events, party.party) !== null,
  );
  const finished = session.state === "CERTIFIED" || session.state === "FAILED";
  const tone = roleTone(party.role);
  return (
    <div className={`card card-pad agent-card tone-${tone}`}>
      <div className="row">
        <span className="grow" style={{ fontWeight: 600 }}>
          {party.display_name} agent
        </span>
        <span className="agent-state" style={finished ? { color: "var(--fg3)" } : undefined}>
          <span className={`dot${finished ? "" : " pulse"}`} />
          {finished ? "done" : "online"}
        </span>
      </div>
      <div className="fg2" style={{ fontSize: 12.5 }}>
        Reads {party.display_name} code &amp; docs only
      </div>
      <div className="now">
        <span className="label">NOW</span>
        <span>{now}</span>
      </div>
      <div className="agent-foot mono">
        {own
          ? "Token Factory sandbox · no network · model API only"
          : `${party.display_name} ${party.role} · Token Factory sandbox`}
      </div>
    </div>
  );
}

function SystemModel({ party }: { party: PartyView }) {
  const { data } = useSessionContext();
  const stats = psm(data.events, party.party);
  return (
    <div className="card card-pad">
      <div className="card-section-head">
        <span className="panel-title">Private system model</span>
        <span className="tiny-tag">never leaves {party.display_name}</span>
      </div>
      {stats ? (
        <div className="stat-grid">
          <div className="stat">
            <div className="v">{stats.files}</div>
            <div className="k">files analysed</div>
          </div>
          <div className="stat">
            <div className="v">{(stats.tokens / 1000).toFixed(1)}k</div>
            <div className="k">tokens read</div>
          </div>
          <div className="stat">
            <div className="v">{Math.round(stats.seconds)}s</div>
            <div className="k">to build</div>
          </div>
        </div>
      ) : (
        <div className="muted">
          The agent builds it inside its own sandbox when the session starts.
        </div>
      )}
    </div>
  );
}

function QuestionCard({ question, canAnswer }: { question: Question; canAnswer: boolean }) {
  const { id, token, data, canAct } = useSessionContext();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const open = !question.answer;
  const answer = async (option: string) => {
    setBusy(option);
    setError(null);
    try {
      await api.answer(id, question.id, token, option);
      data.refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(null);
    }
  };
  const who = question.answered_by?.startsWith("human:") ? "human" : question.answered_by;
  return (
    <div className={`question${open ? " open" : ""}`} data-testid={`question-${question.id}`}>
      <div className="row" style={{ alignItems: "baseline" }}>
        <span className="qid">{question.id}</span>
        <span className="qtext">{question.text}</span>
      </div>
      {open ? (
        <div className="stack" style={{ gap: 6 }}>
          {question.options.map((option) => (
            <button
              key={option}
              type="button"
              className="option"
              disabled={!canAnswer || busy !== null}
              onClick={() => answer(option)}
            >
              <span className="grow">{option}</span>
              {busy === option ? <span className="spinner" /> : null}
              {option === question.default && <span className="rec">DEFAULT</span>}
            </button>
          ))}
          {!canAnswer && canAct && (
            <span className="muted">Switch “View as” to this company to answer.</span>
          )}
          {error && <span className="field-error">{error}</span>}
        </div>
      ) : (
        <div className="answered">
          <span className="ok">✓</span> {question.answer}{" "}
          <span className="muted">
            · answered by {who ?? "a human"}
            {question.decision_id ? ` · decision ${question.decision_id}` : ""}
          </span>
        </div>
      )}
    </div>
  );
}

function Questions({ party, canAnswer }: { party: PartyView; canAnswer: boolean }) {
  const { data } = useSessionContext();
  const questions = data.questions.filter((question) => question.party === party.party);
  const open = questions.filter((question) => !question.answer).length;
  return (
    <div className="card card-pad" data-testid={`questions-${party.party}`}>
      <div className="card-section-head">
        <span className="panel-title">Questions for our humans</span>
        <span className="mono" style={{ fontSize: 11, color: open ? "var(--p)" : "var(--fg3)" }}>
          {open ? `${open} open` : `${questions.length} answered`}
        </span>
      </div>
      {questions.length === 0 && (
        <div className="muted">No questions yet. The agent asks when a decision needs a human.</div>
      )}
      {questions.map((question) => (
        <QuestionCard key={question.id} question={question} canAnswer={canAnswer} />
      ))}
    </div>
  );
}

function Implementation({ party }: { party: PartyView }) {
  const { data } = useSessionContext();
  const steps = coderSteps(data.events, party.party);
  const runs = ofType(data.events, "tests.result").filter(
    (event) => payload<{ party: string }>(event).party === party.party,
  );
  const last = runs.at(-1);
  const results = last ? payload<{ results: { status: string }[] }>(last).results : [];
  const coding = data.session?.state === "IMPLEMENTING" && party.status === "implementing";
  const shown = steps.slice(-7);
  return (
    <div className="card card-pad">
      <div className="card-section-head">
        <span className="panel-title">Implementation</span>
        <span className="mono fg2" style={{ fontSize: 12 }}>
          {steps.length ? `${steps.length} steps` : ""}
        </span>
      </div>
      {steps.length === 0 && !last ? (
        <div className="muted">
          Starts in the isolated sandbox once both companies sign the contract.
        </div>
      ) : (
        <>
          <div className="steps">
            {shown.map((step, index) => {
              const running = coding && index === shown.length - 1;
              return (
                <div key={step.seq} className={`step ${running ? "run" : "done"}`}>
                  <span className="mark">{running ? "" : "✓"}</span>
                  <span className="mono muted" style={{ width: 34 }}>
                    #{step.step}
                  </span>
                  <span className="ellipsis">{step.note}</span>
                </div>
              );
            })}
          </div>
          <div className="facts">
            <div>
              <span>Coder steps</span>
              <span>{steps.length}</span>
            </div>
            <div>
              <span>Files changed · visible only to {party.display_name}</span>
              <span>{steps.at(-1)?.files ?? 0}</span>
            </div>
            <div>
              <span>Contract tests</span>
              <span>
                {results.length
                  ? `${results.filter((item) => item.status === "passed").length}/${results.length} passing`
                  : "not run yet"}
              </span>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function renderBriefValue(value: unknown): string {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    const item = value as { concept?: string; representation?: string };
    if (item.concept)
      return `${item.concept}${item.representation ? ` — ${item.representation}` : ""}`;
  }
  return JSON.stringify(value);
}

function BriefCard({ party }: { party: PartyView }) {
  const { data } = useSessionContext();
  const shared = brief(data.events, party.party);
  const summary = shared ? String(shared.goal ?? shared.summary ?? "") : "";
  const sections = shared
    ? Object.entries(shared).filter(
        ([key, value]) => key in BRIEF_LABELS && Array.isArray(value) && value.length,
      )
    : [];
  return (
    <div className="card card-pad">
      <span className="panel-title">Shared brief</span>
      {!shared ? (
        <div className="muted">
          Not shared yet. The brief is the only description that leaves the company.
        </div>
      ) : (
        <>
          {summary && <div className="brief-text">{summary}</div>}
          {sections.map(([key, value]) => (
            <div key={key} className="stack" style={{ gap: 4 }}>
              <span className="muted">{BRIEF_LABELS[key]}</span>
              <ul className="brief-list">
                {(value as unknown[]).slice(0, 6).map((item, index) => (
                  <li key={index}>{renderBriefValue(item)}</li>
                ))}
              </ul>
            </div>
          ))}
        </>
      )}
    </div>
  );
}

function SharedFacts({ party, session }: { party: PartyView; session: SessionView }) {
  const { data } = useSessionContext();
  const own = turns(data.events).filter((turn) => turn.party === party.party);
  const runs = ofType(data.events, "tests.result").filter(
    (event) => payload<{ party: string }>(event).party === party.party,
  );
  const results = runs.length
    ? payload<{ results: { status: string }[] }>(runs[runs.length - 1]).results
    : [];
  const signed = signedParties(
    data.events,
    session.signed_contract_version ?? session.current_contract_version,
  );
  return (
    <div className="facts plain">
      <div>
        <span>Turns in negotiation</span>
        <span>{own.length}</span>
      </div>
      <div>
        <span>Last proposal</span>
        <span>{own.at(-1) ? `v${own.at(-1)?.version} · ${own.at(-1)?.status}` : "—"}</span>
      </div>
      <div>
        <span>Signature</span>
        <span>{signed.has(party.party) ? "Signed" : "Pending"}</span>
      </div>
      <div>
        <span>Contract tests</span>
        <span>
          {results.length
            ? `${results.filter((item) => item.status === "passed").length}/${results.length} passing`
            : "—"}
        </span>
      </div>
    </div>
  );
}

export function PartyColumn({
  party,
  own,
  position,
}: {
  party: PartyView;
  own: boolean;
  position: "first" | "last";
}) {
  const { data, view, canAct } = useSessionContext();
  const session = data.session;
  if (!session) return null;
  const tone = roleTone(party.role);
  const name = partyName(session, party.party);
  return (
    <div className={`side-col ${position}`} data-testid={`party-${party.party}`}>
      <div className="party-head">
        <Swatch tone={tone} size={8} />
        {own
          ? `Our side · ${name}`
          : view === OBSERVER
            ? `${name} · shared only`
            : "Partner · shared only"}
      </div>
      <AgentCard party={party} own={own} />
      {own ? (
        <>
          <SystemModel party={party} />
          <Questions party={party} canAnswer={view === party.party && canAct} />
          <Implementation party={party} />
        </>
      ) : (
        <>
          <div className="muted" style={{ padding: "0 2px" }}>
            Model and prompts not shared
          </div>
          <BriefCard party={party} />
          <div className="card card-pad">
            <span className="panel-title">What {name} shares</span>
            <SharedFacts party={party} session={session} />
          </div>
          <div className="locked-grid">
            {[
              "System model",
              `Questions to ${name} humans`,
              "Implementation & changed files",
              "Sandbox logs",
            ].map((title) => (
              <LockedTile key={title} title={title} owner={name} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
