import { useState } from "react";
import { api } from "../api/client";
import type { ProbeResult, SessionView } from "../api/types";
import { Card, Empty, Label, Swatch } from "../components/ui";
import { dateTime, shortHash } from "../lib/format";
import { probeResults, roleTone, type Tone } from "../lib/model";
import { useSessionContext } from "../state/session";

const RESULT_TONE: Record<string, Tone> = { passed: "o", failed: "e", skipped: "n" };
const COLUMNS = "36px minmax(240px,1.4fr) 150px minmax(140px,1fr) minmax(200px,1.4fr) 90px";

function runsIn(
  probe: ProbeResult,
  session: SessionView | null,
): { label: string; tone: Tone | "fg3" } {
  const name = probe.name.toLowerCase();
  if (name.includes("gateway")) return { label: "Gateway", tone: "fg3" };
  if (name.includes("party runtime")) return { label: "Party runtime", tone: "fg3" };
  const party = session?.parties.find((item) => name.includes(item.party));
  if (party && name.includes("sandbox"))
    return { label: `${party.display_name} sandbox`, tone: roleTone(party.role) };
  if (party) return { label: party.display_name, tone: roleTone(party.role) };
  return { label: "Token Factory", tone: "fg3" };
}

export function IsolationPage() {
  const { id, stored, data } = useSessionContext();
  const observerToken = stored?.observer ?? null;
  const session = data.session;
  const recorded = probeResults(data.events);
  const [fresh, setFresh] = useState<{ ok: boolean; results: ProbeResult[] } | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const report =
    fresh ?? (recorded ? { ok: recorded.ok, results: recorded.results as ProbeResult[] } : null);
  const results = report?.results ?? [];
  const count = (status: string) => results.filter((item) => item.status === status).length;
  const consumer = session?.parties.find((party) => party.role === "consumer");
  const provider = session?.parties.find((party) => party.role === "provider");
  const version = session?.signed_contract_version ?? session?.current_contract_version;

  const rerun = async () => {
    if (!observerToken) return;
    setRunning(true);
    setError(null);
    try {
      setFresh(await api.isolationProbe(id, observerToken));
      data.refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setRunning(false);
    }
  };

  const box = (party: typeof consumer) =>
    party && (
      <div className={`iso-box tone-${roleTone(party.role)}`}>
        <div className="row">
          <Swatch tone={roleTone(party.role)} size={9} />
          <b>{party.display_name}</b>
        </div>
        <div className="iso-item">
          <span>Agent</span>
          <span>reads only {party.display_name} code</span>
        </div>
        <div className="iso-item">
          <span>Sandbox</span>
          <span>Token Factory · no network</span>
        </div>
        <div className="iso-item">
          <span>Logs & code</span>
          <span>stay in {party.display_name}</span>
        </div>
      </div>
    );

  return (
    <div className="page" style={{ maxWidth: 1560 }} data-testid="isolation-page">
      <div className="page-head">
        <div className="grow">
          <div className="eyebrow">
            {consumer?.display_name} ↔ {provider?.display_name} · {id}
          </div>
          <h1>Isolation proof</h1>
          <div className="sub">
            Active probes run inside each company's sandbox, from the party runtimes and at the
            gateway. Results are appended to the audit chain — they show what each agent could and
            could not reach.
          </div>
        </div>
        <div className="row">
          {recorded && !fresh && <span className="mono muted">{dateTime(recorded.ts)}</span>}
          <button
            type="button"
            className="btn btn-primary"
            onClick={rerun}
            disabled={running || !observerToken}
            title={observerToken ? undefined : "Probes are run by the neutral observer"}
          >
            {running && <span className="spinner" />}
            {running ? "Running probes…" : recorded || fresh ? "Re-run probes" : "Run probes"}
          </button>
        </div>
      </div>
      {!observerToken && (
        <div className="alert">
          Only the neutral observer can run the probes; this browser has no observer token.
        </div>
      )}
      {error && <div className="alert alert-error">{error}</div>}
      <div className="iso-diagram">
        {box(consumer)}
        <div className="iso-mid">
          <div className="iso-gate">
            <div style={{ fontWeight: 600, fontSize: 12.5 }}>Integration gateway</div>
            <div className="mono muted">contract {version ? `v${version}` : "—"} only</div>
          </div>
          <div className="iso-flow" style={{ color: "var(--o)" }}>
            contract + test results
          </div>
          <div className="iso-flow" style={{ color: "var(--e)" }}>
            code · paths · secrets · PII ✕
          </div>
        </div>
        {box(provider)}
      </div>
      {report ? (
        <>
          <div className="row wrap">
            <div className="pill">
              <span className="dot" style={{ width: 8, height: 8, background: "var(--o)" }} />
              <span className="mono" style={{ fontWeight: 600 }}>
                {count("passed")}
              </span>
              <span className="fg2">passed</span>
            </div>
            <div className="pill">
              <span className="dot" style={{ width: 8, height: 8, background: "var(--n)" }} />
              <span className="mono" style={{ fontWeight: 600 }}>
                {count("skipped")}
              </span>
              <span className="fg2">skipped</span>
            </div>
            <div className="pill">
              <span className="dot" style={{ width: 8, height: 8, background: "var(--e)" }} />
              <span className="mono" style={{ fontWeight: 600 }}>
                {count("failed")}
              </span>
              <span className="fg2">failed</span>
            </div>
            <span className="grow" />
            {recorded && (
              <span className="mono muted">
                appended as event #{recorded.seq} · audit{" "}
                {shortHash(data.events.find((event) => event.seq === recorded.seq)?.hash)}
              </span>
            )}
          </div>
          <Card testId="probes">
            <div className="table-wrap">
              <div style={{ minWidth: 960 }}>
                <div className="trow head table-head" style={{ gridTemplateColumns: COLUMNS }}>
                  <span>#</span>
                  <span>Probe</span>
                  <span>Runs in</span>
                  <span>Expected</span>
                  <span>Actual</span>
                  <span>Result</span>
                </div>
                {results.map((probe, index) => {
                  const where = runsIn(probe, session);
                  return (
                    <div key={probe.name} className="trow" style={{ gridTemplateColumns: COLUMNS }}>
                      <span className="mono muted">{index + 1}</span>
                      <span style={{ fontWeight: 500 }}>{probe.name}</span>
                      <span className="row" style={{ gap: 6 }}>
                        <Swatch tone={where.tone} size={7} />
                        {where.label}
                      </span>
                      <span className="mono" style={{ fontSize: 12 }}>
                        {probe.expected}
                      </span>
                      <span className="mono fg2" style={{ fontSize: 12, overflowWrap: "anywhere" }}>
                        {probe.actual}
                      </span>
                      <span>
                        <span
                          className={`badge tone-${RESULT_TONE[probe.status] ?? "n"} mono`}
                          style={{ fontWeight: 600 }}
                        >
                          {probe.status.toUpperCase()}
                        </span>
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          </Card>
        </>
      ) : (
        <Empty title="No probes run in this session yet">
          <Label>Observer</Label> runs them on demand. Each probe tries something an isolated agent
          must not be able to do and records what actually happened.
        </Empty>
      )}
    </div>
  );
}
