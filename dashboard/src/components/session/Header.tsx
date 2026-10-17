import { useState } from "react";
import { duration, usd } from "../../lib/format";
import { elapsedMs, probeResults, roleTone } from "../../lib/model";
import { navigate, sessionPath } from "../../state/route";
import { OBSERVER, useSessionContext } from "../../state/session";
import { shareLink } from "../../state/sessions";
import { Badge, CodeShared, Segmented, StateBadge, Swatch } from "../ui";

export function CopyButton({ text, label = "Copy" }: { text: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      className="btn btn-sm"
      onClick={() => {
        navigator.clipboard?.writeText(text).catch(() => undefined);
        setCopied(true);
        window.setTimeout(() => setCopied(false), 1600);
      }}
    >
      {copied ? "Copied" : label}
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
  if (replay.recorded) {
    return (
      <div className="row" data-testid="recorded-run">
        <Badge tone="w" pulse={replay.position < replay.total}>
          Recorded run
        </Badge>
        <span className="mono muted">
          {replay.position}/{replay.total}
        </span>
      </div>
    );
  }
  return (
    <div className="row" data-testid="replay-bar">
      {replay.active ? (
        <>
          <button type="button" className="btn btn-sm" onClick={replay.toggle}>
            {replay.playing ? "Pause" : replay.position >= replay.total ? "Replay again" : "Play"}
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
          <span className="mono muted">
            {replay.position}/{replay.total}
          </span>
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
          ▶ Replay session
        </button>
      )}
    </div>
  );
}

export function SessionHeader() {
  const { id, stored, view, setView, viewOptions, data, now } = useSessionContext();
  const session = data.session;
  const events = data.events;
  const probes = probeResults(events);
  const metrics = session?.metrics;
  const cost = metrics ? metrics.cost_usd + (metrics.sandbox_cost_usd ?? 0) : 0;
  const version = session?.signed_contract_version ?? session?.current_contract_version;
  const consumer = session?.parties.find((party) => party.role === "consumer");
  const provider = session?.parties.find((party) => party.role === "provider");
  const tests = events.filter((event) => event.type === "e2e.result").at(-1);
  const e2e = tests
    ? ((tests.payload.report as { scenarios?: { status: string }[] }).scenarios ?? [])
    : [];
  const probeLabel = probes
    ? `${probes.results.filter((item) => item.status === "passed").length}/${probes.results.length}`
    : "run";

  return (
    <div className="session-head">
      <div className="row wrap" style={{ alignItems: "flex-start", gap: 16 }}>
        <div className="grow stack" style={{ minWidth: 300, gap: 6 }}>
          <div className="eyebrow">
            <span>{id}</span>
            {session && <StateBadge state={session.state} />}
          </div>
          <h1 className="session-title">{session?.name ?? stored?.name ?? id}</h1>
          {consumer && provider && (
            <div className="parties-line">
              <span className="row" style={{ gap: 6 }}>
                <Swatch tone="a" />
                {consumer.display_name}
              </span>
              <span className="role">consumes</span>
              <span className="role">↔</span>
              <span className="row" style={{ gap: 6 }}>
                <Swatch tone="b" />
                {provider.display_name}
              </span>
              <span className="role">provides</span>
            </div>
          )}
          {session?.scope && (
            <div className="muted" data-testid="session-scope">
              Scope: {session.scope}
            </div>
          )}
        </div>
        <div className="row wrap">
          <CodeShared lines={metrics?.code_lines_shared ?? 0} />
          <button
            type="button"
            className="btn btn-lg"
            onClick={() => navigate(sessionPath(id, "isolation"))}
          >
            <span
              style={{ width: 10, height: 10, border: "1.5px solid var(--o)", borderRadius: 2 }}
            />
            Isolation proof
            <span
              className="mono"
              style={{ fontSize: 11, color: probes?.ok === false ? "var(--e)" : "var(--o)" }}
            >
              {probeLabel}
            </span>
          </button>
          <button
            type="button"
            className="btn btn-lg"
            onClick={() => navigate(sessionPath(id, "contract"))}
          >
            Open contract
          </button>
          {stored && <CopyButton text={shareLink(stored)} label="Copy link" />}
        </div>
      </div>
      <div className="row wrap" style={{ gap: 20 }}>
        <div className="metrics">
          <div>
            <span className="label">Round</span>
            <div className="value">
              {session ? `${session.round} / ${session.max_rounds}` : "—"}
            </div>
          </div>
          <div>
            <span className="label">Elapsed</span>
            <div className="value">{duration(elapsedMs(events, session?.state, now))}</div>
          </div>
          <div>
            <span className="label">Cost</span>
            <div className="value">{usd(cost)}</div>
          </div>
          <div>
            <span className="label">Contract</span>
            <div className="value">
              {version ? `v${version}${session?.signed_contract_version ? " · signed" : ""}` : "—"}
            </div>
          </div>
          <div>
            <span className="label">End-to-end</span>
            <div className="value">
              {e2e.length
                ? `${e2e.filter((item) => item.status === "passed").length}/${e2e.length} green`
                : "—"}
            </div>
          </div>
        </div>
        <span className="grow" />
        <ReplayBar />
        <div className="row">
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
      {view === OBSERVER && (
        <div className="alert">
          <b>READ-ONLY</b>
          Observer view: shared data from both companies, costs and the audit trail — never either
          side's internals.
        </div>
      )}
      {data.error && <div className="alert alert-error">{data.error}</div>}
      <AgentsConnect />
    </div>
  );
}
