import { useState } from "react";
import { duration, usd } from "../../lib/format";
import { elapsedMs, roleTone } from "../../lib/model";
import { navigate, sessionPath } from "../../state/route";
import { OBSERVER, useSessionContext } from "../../state/session";
import { shareLink } from "../../state/sessions";
import { Segmented, StateBadge, Swatch } from "../ui";
import { StageBar } from "./StageBar";

export function CopyButton({
  text,
  label = "Copy",
  plain = false,
}: {
  text: string;
  label?: string;
  plain?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className={plain ? "btn btn-plain" : "btn btn-sm"}
      onClick={() => {
        navigator.clipboard?.writeText(text).catch(() => undefined);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      }}
    >
      {copied ? (plain ? "Link copied" : "Copied") : label}
    </button>
  );
}

function AgentsConnect() {
  const { id, stored, data } = useSessionContext();
  const session = data.session;
  if (!session || !stored || Object.keys(stored.agents).length === 0) return null;
  const waiting = session.parties.filter((party) => !party.brief_submitted);
  if (!["CREATED", "BRIEFING"].includes(session.state) || waiting.length === 0) return null;
  return (
    <div className="alert alert-warn connect" data-testid="agents-connect">
      <div className="row wrap">
        <b>WAITING FOR AGENTS</b>
        <span>
          Each company starts its own agent with its own token. The broker never sees their code.
        </span>
      </div>
      {waiting.map((party) => {
        const token = stored.agents[party.party];
        if (!token) return null;
        const command = `docker compose --profile party run --rm -e SESSION_ID=${id} -e AGENT_TOKEN=${token} -e SANDBOX_BASE_TAG=<base image tag> party-${party.party}`;
        return (
          <div key={party.party} className="cmd">
            <Swatch tone={roleTone(party.role)} />
            <code>{command}</code>
            <CopyButton text={command} />
          </div>
        );
      })}
    </div>
  );
}

export function ReplayBar() {
  const { replay } = useSessionContext();
  const progress = (
    <span className="replay-progress mono">
      <span>
        <span style={{ width: `${(replay.position / Math.max(replay.total, 1)) * 100}%` }} />
      </span>
      {replay.position}/{replay.total}
    </span>
  );
  if (replay.recorded) {
    return (
      <div className="row" data-testid="recorded-run">
        <span className="outline-tag">Recorded run</span>
        {progress}
      </div>
    );
  }
  return (
    <div className="row wrap" data-testid="replay-bar">
      {replay.active ? (
        <>
          <span className="outline-tag">Recorded run</span>
          {progress}
          <button type="button" className="btn btn-sm" onClick={replay.toggle}>
            {replay.playing
              ? "Pause"
              : replay.position >= replay.total
                ? "↻ Replay session"
                : "Play"}
          </button>
          <Segmented
            small
            value={replay.speed}
            onChange={replay.setSpeed}
            options={[
              { value: "slow", label: "0.5×" },
              { value: "normal", label: "1×" },
              { value: "fast", label: "4×" },
            ]}
          />
          <button type="button" className="btn btn-ghost btn-sm" onClick={replay.stop}>
            Back to live
          </button>
        </>
      ) : (
        <button
          type="button"
          className="btn btn-sm"
          onClick={replay.start}
          disabled={replay.total === 0}
          title="Play the recorded event log from the start"
        >
          ↻ Replay session
        </button>
      )}
    </div>
  );
}

export function SessionHeader() {
  const { id, stored, view, setView, viewOptions, data, now } = useSessionContext();
  const session = data.session;
  const events = data.events;
  const metrics = session?.metrics;
  const cost = metrics ? metrics.cost_usd + (metrics.sandbox_cost_usd ?? 0) : 0;
  const version = session?.signed_contract_version ?? session?.current_contract_version;
  const consumer = session?.parties.find((party) => party.role === "consumer");
  const provider = session?.parties.find((party) => party.role === "provider");
  const tests = events.filter((event) => event.type === "e2e.result").at(-1);
  const e2e = tests
    ? ((tests.payload.report as { scenarios?: { status: string }[] }).scenarios ?? [])
    : [];
  const e2eGreen = e2e.filter((item) => item.status === "passed").length;
  const contractText = !version
    ? "—"
    : session?.signed_contract_version
      ? `v${version} signed`
      : `v${version} ${session?.state === "AWAITING_SIGNATURES" ? "converged" : "draft"}`;

  return (
    <div className="session-head">
      <div className="row wrap" style={{ alignItems: "flex-start", gap: 16 }}>
        <div className="stack" style={{ flex: "1 1 420px", minWidth: 0, gap: 6 }}>
          <div className="eyebrow">
            <span>{id}</span>
            {session && <StateBadge state={session.state} />}
            <ReplayBar />
          </div>
          <h1 className="session-title">{session?.name ?? stored?.name ?? id}</h1>
          {consumer && provider && (
            <div className="parties-line">
              <span className="row" style={{ gap: 6 }}>
                <Swatch tone="a" size={8} />
                {consumer.display_name} consumes
              </span>
              <span className="role">↔</span>
              <span className="row" style={{ gap: 6 }}>
                <Swatch tone="b" size={8} />
                {provider.display_name} provides
              </span>
            </div>
          )}
          {session?.scope && (
            <div className="session-scope" data-testid="session-scope">
              <span className="muted" style={{ fontSize: 13 }}>
                Scope:
              </span>{" "}
              {session.scope}
            </div>
          )}
        </div>
        <div className="stack" style={{ gap: 8, alignItems: "flex-end" }}>
          <div className="row">
            <button
              type="button"
              className="btn btn-plain"
              onClick={() => navigate(sessionPath(id, "contract"))}
            >
              Open contract
            </button>
            {stored && <CopyButton text={shareLink(stored)} label="Copy link" plain />}
          </div>
          <div className="row wrap" style={{ justifyContent: "flex-end" }}>
            {view !== OBSERVER && (
              <span className="label" style={{ fontWeight: 600, letterSpacing: "0.08em" }}>
                COMPANY VIEW
              </span>
            )}
            <span className="muted">View as</span>
            <Segmented
              value={view}
              onChange={setView}
              options={viewOptions.map((option) => ({
                value: option.value,
                disabled: option.disabled,
                title: option.disabled ? "No token for this view in this browser" : undefined,
                label: (
                  <>
                    <Swatch tone={option.tone} size={7} />
                    {option.label}
                  </>
                ),
              }))}
            />
          </div>
        </div>
      </div>
      {view === OBSERVER && (
        <div className="alert">
          <span>
            <b>Read-only:</b> shared data from both companies, costs and the audit trail — never
            either side's internals.
          </span>
        </div>
      )}
      <div className="metrics">
        <div>
          Round
          <div
            className="value"
            style={session?.state === "ESCALATED" ? { color: "var(--w)" } : undefined}
          >
            {session ? `${session.round} / ${session.max_rounds}` : "—"}
          </div>
        </div>
        <div>
          Elapsed
          <div className="value">{duration(elapsedMs(events, session?.state, now))}</div>
        </div>
        <div>
          Cost
          <div className="value">{usd(cost)}</div>
        </div>
        <div>
          Contract
          <div className="value">{contractText}</div>
        </div>
        <div>
          End-to-end
          <div
            className="value"
            style={e2e.length && e2eGreen === e2e.length ? { color: "var(--o)" } : undefined}
          >
            {e2e.length ? `${e2eGreen}/${e2e.length}` : "—"}
          </div>
        </div>
      </div>
      <StageBar />
      {data.error && <div className="alert alert-error">{data.error}</div>}
      <AgentsConnect />
    </div>
  );
}
