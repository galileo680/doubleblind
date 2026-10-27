import { useEffect, useState } from "react";
import { api, certificateHtmlUrl } from "../api/client";
import type { Certificate, CertificateRun, ScenarioRun, TestResult } from "../api/types";
import { Empty, Label, Logo, Skeleton, Swatch } from "../components/ui";
import { dateTime, duration, shortHash, usd } from "../lib/format";
import { elapsedMs, ofType, roleTone } from "../lib/model";
import { useSessionContext } from "../state/session";

function runResults(run: CertificateRun | undefined): { id: string; status: string }[] {
  if (!run) return [];
  if (Array.isArray(run.results))
    return (run.results as TestResult[]).map((item) => ({
      id: item.scenario_id,
      status: item.status,
    }));
  return ((run.results as { scenarios?: ScenarioRun[] }).scenarios ?? []).map((item) => ({
    id: item.id,
    status: item.status,
  }));
}

export function CertificatePage() {
  const { id, token, data, now } = useSessionContext();
  const session = data.session;
  const [certificate, setCertificate] = useState<Certificate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const issued = ofType(data.events, "certificate.issued").length;
  const state = session?.state;

  useEffect(() => {
    let cancelled = false;
    api
      .certificate(id, token)
      .then((value) => !cancelled && setCertificate(value))
      .catch(
        (failure: unknown) =>
          !cancelled && setError(failure instanceof Error ? failure.message : String(failure)),
      );
    return () => {
      cancelled = true;
    };
  }, [id, token, issued, state]);

  if (error)
    return (
      <div className="page">
        <div className="alert alert-error">{error}</div>
      </div>
    );
  if (!certificate || !session)
    return (
      <div className="cert-page">
        <div className="cert">
          <Skeleton />
        </div>
      </div>
    );

  const consumer = session.parties.find((party) => party.role === "consumer");
  const provider = session.parties.find((party) => party.role === "provider");
  const runs = {
    consumer: runResults(certificate.results.consumer),
    provider: runResults(certificate.results.provider),
    e2e: runResults(certificate.results.e2e),
  };
  const all = [...runs.consumer, ...runs.provider, ...runs.e2e];
  const passed = all.filter((item) => item.status === "passed").length;
  const issuedEvent = ofType(data.events, "certificate.issued").at(-1);
  const signatures = ofType(data.events, "signature.added").filter(
    (event) => event.payload.version === certificate.signed_contract_version,
  );
  const chain = data.events.length
    ? [
        data.events[0],
        ...data.events
          .filter(
            (_, index) =>
              index > 0 && index % Math.max(1, Math.floor(data.events.length / 4)) === 0,
          )
          .slice(0, 3),
        data.events[data.events.length - 1],
      ]
    : [];
  const uniqueChain = chain.filter(
    (event, index) => chain.findIndex((item) => item.seq === event.seq) === index,
  );
  const tone = certificate.certified ? "o" : "w";
  const runTone = { consumer: "a", provider: "b", e2e: "n" } as const;

  return (
    <div className="cert-page" data-testid="certificate-page">
      <div className="cert-bar">
        <div
          className="row grow"
          style={{
            height: 34,
            padding: "0 12px",
            border: "1px solid var(--line)",
            borderRadius: 8,
            background: "var(--s1)",
          }}
        >
          <Label>Session</Label>
          <span className="mono ellipsis grow">{id}</span>
        </div>
        <a
          className="btn btn-lg"
          href={certificateHtmlUrl(id, token)}
          target="_blank"
          rel="noreferrer"
        >
          Open broker HTML
        </a>
        <button type="button" className="btn btn-primary btn-lg" onClick={() => window.print()}>
          Print / PDF
        </button>
      </div>
      {!certificate.certified && (
        <div className="alert alert-warn" style={{ width: "100%", maxWidth: 960 }}>
          <b>NOT CERTIFIED YET</b>
          The certificate is issued when every consumer, provider and end-to-end scenario is green.
          Current state: {certificate.state}.
        </div>
      )}
      <article className="cert">
        <div className="cert-top">
          <div className="grow" style={{ minWidth: 280 }}>
            <div className="row" style={{ gap: 10 }}>
              <Logo size={16} />
              <span style={{ fontWeight: 600, letterSpacing: "-0.01em" }}>DoubleBlind</span>
            </div>
            <div className="label" style={{ marginTop: 28, letterSpacing: "0.1em", fontSize: 11 }}>
              Certificate of integration
            </div>
            <h1>
              {consumer?.display_name} ↔ {provider?.display_name}
            </h1>
            <div className="fg2" style={{ fontSize: 14 }}>
              {session.name} · contract v{certificate.signed_contract_version ?? "—"}
            </div>
          </div>
          <div className={`seal tone-${tone}`}>
            <div>
              <div className="k">{certificate.certified ? "CERTIFIED" : "PENDING"}</div>
              <div className="v">
                {passed}/{all.length}
              </div>
              <div className="mono muted">
                {issuedEvent ? new Date(issuedEvent.ts).toLocaleDateString() : "—"}
              </div>
            </div>
          </div>
        </div>
        <div className="cert-facts">
          <div>
            <Label>Scenarios</Label>
            <div
              className="v"
              style={{ color: passed === all.length && all.length ? "var(--o)" : "var(--fg)" }}
            >
              {passed}/{all.length}
            </div>
            <div className="muted">green on 3 runners</div>
          </div>
          <div>
            <Label>Duration</Label>
            <div className="v">{duration(elapsedMs(data.events, session.state, now))}</div>
            <div className="muted">from session start</div>
          </div>
          <div>
            <Label>Cost</Label>
            <div className="v">{usd(certificate.costs.total_usd)}</div>
            <div className="muted">models + sandboxes</div>
          </div>
          <div>
            <Label>Gateway</Label>
            <div
              className="v"
              style={{ color: certificate.gateway.schema_violations ? "var(--w)" : "var(--o)" }}
            >
              {certificate.gateway.requests - certificate.gateway.refused}
            </div>
            <div className="muted">
              requests within contract · {certificate.gateway.refused} off-contract refused ·{" "}
              {certificate.gateway.schema_violations} violations
            </div>
          </div>
        </div>
        <div
          className="cert-section"
          style={{
            display: "grid",
            gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
            gap: 20,
          }}
        >
          {[consumer, provider].map(
            (party) =>
              party && (
                <div key={party.party} className="stack" style={{ gap: 8 }}>
                  <div className="row">
                    <Swatch tone={roleTone(party.role)} size={9} />
                    <b>{party.display_name}</b>
                    <span className="muted">
                      {party.role === "consumer" ? "Consumer" : "Provider"}
                    </span>
                  </div>
                  {signatures
                    .filter((event) => event.payload.party === party.party)
                    .map((event) => (
                      <div
                        key={event.seq}
                        className="stack"
                        style={{
                          gap: 2,
                          padding: "10px 12px",
                          border: "1px solid var(--line)",
                          borderRadius: 8,
                          background: "var(--s2)",
                        }}
                      >
                        <div className="row">
                          <span style={{ fontWeight: 500 }} className="grow">
                            {party.display_name} approver
                          </span>
                          <span className="mono" style={{ fontSize: 11.5, color: "var(--o)" }}>
                            ✓ signed
                          </span>
                        </div>
                        <div className="mono muted" style={{ fontSize: 11 }}>
                          event #{event.seq} · {shortHash(event.hash)} · {dateTime(event.ts)}
                        </div>
                      </div>
                    ))}
                </div>
              ),
          )}
        </div>
        <div className="cert-section">
          <div className="row wrap">
            <b className="grow">Scenarios</b>
            <span className="muted">
              Run on each company's sandbox and end-to-end between the real systems
            </span>
          </div>
          <div className="cert-scen">
            {(Object.keys(runs) as (keyof typeof runs)[]).flatMap((side) =>
              runs[side].map((item) => (
                <div
                  key={`${side}-${item.id}`}
                  className={`tone-${item.status === "passed" ? "o" : "e"}`}
                >
                  <span
                    className="swatch"
                    style={{
                      width: 6,
                      height: 6,
                      borderRadius: "50%",
                      background: `var(--${runTone[side]})`,
                    }}
                  />
                  {item.id}
                  <span style={{ marginLeft: "auto" }}>{item.status === "passed" ? "✓" : "✕"}</span>
                </div>
              )),
            )}
          </div>
        </div>
        <div className="cert-section">
          <div className="row wrap">
            <b className="grow">Audit hash chain</b>
            <span
              className="mono"
              style={{ fontSize: 12, color: certificate.audit.ok ? "var(--o)" : "var(--e)" }}
            >
              {certificate.audit.ok ? "✓ verified" : `✕ broken at #${certificate.audit.broken_at}`}{" "}
              · {certificate.audit.events} events
            </span>
          </div>
          <div className="chain">
            {uniqueChain.map((event, index) => (
              <div key={event.seq} className="row" style={{ gap: 0 }}>
                <div className="chain-node">
                  <div className="muted" style={{ fontSize: 10.5 }}>
                    #{event.seq} {event.type}
                  </div>
                  <div>{shortHash(event.hash)}</div>
                </div>
                {index < uniqueChain.length - 1 && <span className="chain-link" />}
              </div>
            ))}
          </div>
          <div className="mono muted" style={{ fontSize: 11.5, wordBreak: "break-all" }}>
            contract sha256 {certificate.contract_sha256 ?? "—"} · last event{" "}
            {certificate.audit.last_hash}
          </div>
        </div>
        <div className="cert-foot">
          <div className="row" style={{ alignItems: "baseline", gap: 10 }}>
            <span
              className="mono"
              style={{
                fontWeight: 600,
                fontSize: 28,
                color: certificate.metrics.code_lines_shared ? "var(--e)" : "var(--o)",
              }}
            >
              {certificate.metrics.code_lines_shared}
            </span>
            <span style={{ fontWeight: 500 }}>lines of code shared</span>
          </div>
          <span className="muted grow" style={{ fontSize: 12.5, minWidth: 240 }}>
            Only the contract and test results crossed the company boundary.{" "}
            {certificate.metrics.blocked_count} outbound messages were blocked and{" "}
            {certificate.metrics.redacted_count} redacted by the egress guard.
          </span>
        </div>
      </article>
      {all.length === 0 && (
        <Empty title="No test results yet">
          They appear once both sides implement the contract.
        </Empty>
      )}
    </div>
  );
}
