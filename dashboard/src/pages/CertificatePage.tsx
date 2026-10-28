import { useEffect, useMemo, useState } from "react";
import { api, certificateHtmlUrl } from "../api/client";
import type { Certificate } from "../api/types";
import { Empty, Logo, Skeleton } from "../components/ui";
import { clock, duration, usd } from "../lib/format";
import { elapsedMs, matrix, ofType, roleTone, runResults, type CellStatus } from "../lib/model";
import { useSessionContext } from "../state/session";
import { useContract } from "../state/useContract";

const MARK: Record<CellStatus, string> = { pass: "✓", fail: "✗", running: "…", idle: "…", na: "—" };

export function CertificatePage() {
  const { id, token, data, now } = useSessionContext();
  const session = data.session;
  const [certificate, setCertificate] = useState<Certificate | null>(null);
  const [error, setError] = useState<string | null>(null);
  const issued = ofType(data.events, "certificate.issued").length;
  const state = session?.state;
  const signedVersion = session?.signed_contract_version ?? null;
  const { contract } = useContract(id, token, signedVersion, signedVersion ?? 0);
  const rows = useMemo(
    () => matrix(contract?.scenarios ?? [], data.events, session),
    [contract, data.events, session],
  );

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
  const all = ["consumer", "provider", "e2e"].flatMap((side) =>
    runResults(certificate.results[side]),
  );
  const passed = all.filter((item) => item.status === "passed").length;
  const issuedEvent = ofType(data.events, "certificate.issued").at(-1);
  const signed = new Set(
    ofType(data.events, "signature.added")
      .filter((event) => event.payload.version === certificate.signed_contract_version)
      .map((event) => String(event.payload.party)),
  );
  const first = data.events[0];
  const last = data.events.at(-1);
  const seal = certificate.certified ? "o" : "w";
  const columns = [
    ["consumer", consumer?.display_name ?? "Consumer"],
    ["provider", provider?.display_name ?? "Provider"],
    ["e2e", "E2E"],
  ] as const;

  return (
    <div className="cert-page" data-testid="certificate-page">
      <div className="cert-bar">
        <span className="grow" />
        <a
          className="btn btn-plain"
          href={certificateHtmlUrl(id, token)}
          target="_blank"
          rel="noreferrer"
        >
          Open broker HTML
        </a>
        <button type="button" className="btn btn-primary" onClick={() => window.print()}>
          Print / PDF
        </button>
      </div>
      {!certificate.certified && (
        <div className="cert-pending">NOT CERTIFIED YET · current state {certificate.state}</div>
      )}
      <article className="cert">
        <div className="cert-stripe" />
        <div className="cert-top">
          <div className="stack" style={{ flex: "1 1 360px", minWidth: 0, gap: 10 }}>
            <div className="row" style={{ gap: 10 }}>
              <Logo size={21} />
              <span style={{ fontWeight: 700 }}>DoubleBlind</span>
            </div>
            <div className="cert-label">Certificate of integration</div>
            <h1>
              {consumer?.display_name} ↔ {provider?.display_name}
            </h1>
            <div className="fg2" style={{ fontSize: 15 }}>
              {session.name} · contract v{certificate.signed_contract_version ?? "—"}
            </div>
          </div>
          <div className={`seal tone-${seal}`}>
            <span className="k">{certificate.certified ? "CERTIFIED" : "PENDING"}</span>
            <span className="v">
              {passed}/{all.length}
            </span>
            <span className="d">
              {issuedEvent ? new Date(issuedEvent.ts).toLocaleDateString() : "in progress"}
            </span>
          </div>
        </div>
        <div className="cert-facts">
          <div>
            Scenarios
            <div className="v">
              {passed} / {all.length}
            </div>
            <div className="s">green on 3 runners</div>
          </div>
          <div>
            Duration
            <div className="v">{duration(elapsedMs(data.events, session.state, now))}</div>
            <div className="s">scope to certificate</div>
          </div>
          <div>
            Cost
            <div className="v">{usd(certificate.costs.total_usd)}</div>
            <div className="s">models + sandboxes</div>
          </div>
          <div>
            Gateway
            <div className="v">
              {certificate.gateway.requests - certificate.gateway.refused} ·{" "}
              {certificate.gateway.refused} · {certificate.gateway.schema_violations}
            </div>
            <div className="s">within contract · off-contract refused · violations</div>
          </div>
        </div>
        <div className="cert-section cert-parties">
          {[consumer, provider].map((party) => {
            if (!party) return null;
            const results = runResults(certificate.results[party.role]);
            const green = results.filter((item) => item.status === "passed").length;
            return (
              <div key={party.party} className={`cert-party tone-${roleTone(party.role)}`}>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span style={{ fontWeight: 600, fontSize: 15 }}>{party.display_name}</span>
                  <span className="muted" style={{ fontSize: 12.5 }}>
                    {party.role}
                  </span>
                </div>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span className="fg2">Contract tests</span>
                  <span className="mono" style={{ fontWeight: 600, color: "var(--o)" }}>
                    {green} / {results.length} green
                  </span>
                </div>
                <div className="row" style={{ justifyContent: "space-between" }}>
                  <span className="fg2">Signed contract</span>
                  <span className="mono">
                    {signed.has(party.party) ? `v${certificate.signed_contract_version}` : "—"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
        <div className="cert-section">
          <div style={{ fontWeight: 600, marginBottom: 8 }}>Scenarios</div>
          {rows.map((row) => (
            <div key={row.id} className="cert-scen">
              <span className="mono muted" style={{ width: 36, fontWeight: 600 }}>
                {row.id}
              </span>
              <span style={{ flex: "1 1 240px" }}>{row.title}</span>
              <span className="mono" style={{ fontSize: 12, color: "var(--o)" }}>
                {columns.map(([column, label]) => `${label} ${MARK[row.cells[column]]}`).join("  ")}
              </span>
            </div>
          ))}
        </div>
        <div className="cert-section">
          <div className="row wrap" style={{ alignItems: "baseline" }}>
            <span className="grow" style={{ fontWeight: 600 }}>
              Audit hash chain
            </span>
            <span style={{ fontSize: 12.5, color: certificate.audit.ok ? "var(--o)" : "var(--e)" }}>
              {certificate.audit.events} events ·{" "}
              {certificate.audit.ok
                ? "chain verified"
                : `chain broken at #${certificate.audit.broken_at}`}
            </span>
          </div>
          <div className="cert-chain">
            {first && (
              <div>
                <div className="muted">First event · {clock(first.ts)}</div>
                <div className="mono">sha256:{first.hash}</div>
              </div>
            )}
            {last && (
              <div>
                <div className="muted">Last event · {clock(last.ts)}</div>
                <div className="mono">sha256:{certificate.audit.last_hash}</div>
              </div>
            )}
          </div>
        </div>
        <div className="cert-foot">
          Only the contract and test results crossed the company boundary.{" "}
          {certificate.metrics.blocked_count} outbound messages were blocked and{" "}
          {certificate.metrics.redacted_count} redacted by the egress guard.
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
