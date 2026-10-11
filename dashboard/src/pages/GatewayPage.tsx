import { useMemo, useState } from "react";
import { Card, CardHead, Empty, Kpi, Label, StateBadge, Swatch } from "../components/ui";
import { clock } from "../lib/format";
import { gatewayRequests, ofType, partyOf, roleTone, type GatewayRequest } from "../lib/model";
import { useSessionContext } from "../state/session";

const COLUMNS =
  "76px 230px 58px minmax(200px,1fr) 56px 70px minmax(150px,0.8fr) minmax(150px,0.8fr)";

function percentile(values: number[], share: number): number {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  return sorted[Math.min(sorted.length - 1, Math.floor(share * sorted.length))];
}

function verdict(request: GatewayRequest): { text: string; tone: "o" | "w" | "e" } {
  if (!request.allowed) return { text: "REJECTED · off-contract", tone: "e" };
  if (request.status === 504) return { text: "Timeout · party did not answer", tone: "e" };
  if (request.violations.length)
    return { text: `${request.violations.length} schema violation(s)`, tone: "w" };
  return { text: "Valid", tone: "o" };
}

export function GatewayPage() {
  const { id, data } = useSessionContext();
  const session = data.session;
  const requests = useMemo(() => gatewayRequests(data.events), [data.events]);
  const [selected, setSelected] = useState<number | null>(null);
  const runs = ofType(data.events, "e2e.result");
  const lastRun = runs.at(-1);
  const scenarios = lastRun
    ? ((lastRun.payload.report as { scenarios?: { id: string; status: string }[] }).scenarios ?? [])
    : [];
  const offContract = requests.filter((request) => !request.allowed);
  const violations = requests.filter((request) => request.violations.length);
  const p95 = percentile(
    requests.filter((request) => request.allowed).map((request) => request.ms),
    0.95,
  );
  const current = requests.find((request) => request.seq === selected) ?? requests.at(-1);
  const consumer = session?.parties.find((party) => party.role === "consumer");
  const provider = session?.parties.find((party) => party.role === "provider");

  const bySource = useMemo(() => {
    const groups = new Map<string, number[]>();
    for (const request of requests.filter((item) => item.allowed)) {
      groups.set(request.source, [...(groups.get(request.source) ?? []), request.ms]);
    }
    return [...groups.entries()].map(([source, values]) => ({
      source,
      p50: percentile(values, 0.5),
      p95: percentile(values, 0.95),
    }));
  }, [requests]);
  const scale = Math.max(800, ...bySource.map((item) => item.p95));

  return (
    <div className="page" data-testid="gateway-page">
      <div className="page-head">
        <div className="grow">
          <div className="eyebrow">
            <span>
              {id} · {runs.length ? `run e2e-${runs.length}` : "no end-to-end run yet"}
            </span>
            {session && <StateBadge state={session.state} />}
          </div>
          <h1>Live gateway traffic</h1>
          <div className="sub">
            {consumer?.display_name ?? "Consumer"} ↔ {provider?.display_name ?? "Provider"} through
            the DoubleBlind Integration Gateway. Every request is matched against the signed
            contract and its schemas; bodies stay with each company.
          </div>
        </div>
      </div>
      <div className="kpis">
        <Kpi label="Requests" value={requests.length} />
        <Kpi label="Within contract" value={requests.length - offContract.length} tone="o" />
        <Kpi label="Rejected off-contract" value={offContract.length} tone="e" />
        <Kpi label="Schema violations" value={violations.length} tone="w" />
        <Kpi label="p95 latency" value={`${p95} ms`} />
        <Kpi
          label="E2E scenarios"
          value={
            scenarios.length
              ? `${scenarios.filter((item) => item.status === "passed").length} / ${scenarios.length}`
              : "—"
          }
        />
      </div>
      {requests.length === 0 ? (
        <Empty title="No gateway traffic yet">
          The end-to-end run starts when both companies have green contract tests. Each company's
          sandbox opens an outbound tunnel to the gateway; nothing else connects them.
        </Empty>
      ) : (
        <div className="row wrap" style={{ alignItems: "flex-start", gap: 16 }}>
          <Card className="gw-main" testId="gateway-requests">
            <CardHead title="Requests" note="Off-contract requests are rejected at the gateway">
              <span className="live">
                <span
                  className={`dot${session?.state === "E2E" ? " pulse" : ""}`}
                  style={{ background: session?.state === "E2E" ? "var(--o)" : "var(--fg3)" }}
                />
                {session?.state === "E2E" ? "Live" : "Recorded"}
              </span>
            </CardHead>
            <div className="table-wrap">
              <div style={{ minWidth: 900 }}>
                <div className="trow head table-head" style={{ gridTemplateColumns: COLUMNS }}>
                  <span>Time</span>
                  <span>Direction</span>
                  <span>Method</span>
                  <span>Path</span>
                  <span>Status</span>
                  <span>Latency</span>
                  <span>Contract</span>
                  <span>Check</span>
                </div>
                <div className="scroll" style={{ maxHeight: 520 }}>
                  {[...requests].reverse().map((request) => {
                    const target = partyOf(session, request.party);
                    const origin = request.source.startsWith("operation")
                      ? consumer
                      : request.source.startsWith("webhook")
                        ? provider
                        : null;
                    const check = verdict(request);
                    return (
                      <div
                        key={request.seq}
                        className={`trow click mono${current?.seq === request.seq ? " sel" : ""}${request.allowed ? "" : " bad"}`}
                        style={{ gridTemplateColumns: COLUMNS, fontSize: 12, padding: "7px 16px" }}
                        onClick={() => setSelected(request.seq)}
                      >
                        <span className="muted">{clock(request.ts)}</span>
                        <span className="row" style={{ fontFamily: "var(--sans)", gap: 5 }}>
                          {origin ? (
                            <Swatch tone={roleTone(origin.role)} size={7} />
                          ) : (
                            <Swatch tone="fg3" size={7} />
                          )}
                          {origin?.display_name ?? "Test runner"}
                          <span className="muted">→</span>
                          <Swatch tone={roleTone(target?.role)} size={7} />
                          {target?.display_name ?? request.party}
                        </span>
                        <span
                          className="method"
                          style={{ color: request.method === "GET" ? "var(--a)" : "var(--o)" }}
                        >
                          {request.method}
                        </span>
                        <span className="ellipsis">{request.path}</span>
                        <span
                          style={{
                            fontWeight: 600,
                            color:
                              request.status >= 500 || !request.allowed
                                ? "var(--e)"
                                : request.status >= 400
                                  ? "var(--w)"
                                  : "var(--fg)",
                          }}
                        >
                          {request.status}
                        </span>
                        <span className="fg2">{request.ms} ms</span>
                        <span className="ellipsis muted">{request.source}</span>
                        <span
                          className="ellipsis"
                          style={{ fontFamily: "var(--sans)", color: `var(--${check.tone})` }}
                        >
                          {check.text}
                        </span>
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>
          </Card>
          <div className="stack" style={{ flex: "1 1 340px", minWidth: 300 }}>
            {current && (
              <Card testId="gateway-detail">
                <CardHead title="Request detail" note={`#${current.seq}`} />
                <div className="card-pad">
                  <div className="mono" style={{ fontWeight: 600 }}>
                    <span style={{ color: current.method === "GET" ? "var(--a)" : "var(--o)" }}>
                      {current.method}
                    </span>{" "}
                    {current.path}
                  </div>
                  {!current.allowed && (
                    <div className="alert alert-error">
                      Rejected — not in the signed contract. The gateway returned 403 before the
                      request reached {partyOf(session, current.party)?.display_name}. No data
                      crossed the boundary.
                    </div>
                  )}
                  <div className="facts">
                    <div>
                      <span>Target</span>
                      <span>{partyOf(session, current.party)?.display_name ?? current.party}</span>
                    </div>
                    <div>
                      <span>Matched by</span>
                      <span>{current.source}</span>
                    </div>
                    <div>
                      <span>Status</span>
                      <span>{current.status}</span>
                    </div>
                    <div>
                      <span>Latency</span>
                      <span>{current.ms} ms</span>
                    </div>
                    <div>
                      <span>Schema check</span>
                      <span style={{ color: `var(--${verdict(current).tone})` }}>
                        {verdict(current).text}
                      </span>
                    </div>
                  </div>
                  {current.violations.length > 0 && (
                    <div className="stack" style={{ gap: 4 }}>
                      <Label>Violations</Label>
                      {current.violations.map((violation) => (
                        <div
                          key={violation}
                          className="mono"
                          style={{ fontSize: 12, color: "var(--w)" }}
                        >
                          {violation}
                        </div>
                      ))}
                    </div>
                  )}
                  <div className="muted">
                    The gateway sees method, path, status and schema validity. Payload values stay
                    inside each company's sandbox.
                  </div>
                </div>
              </Card>
            )}
            <Card>
              <CardHead title="Latency by contract element" />
              <div className="card-pad">
                {bySource.map((item) => (
                  <div
                    key={item.source}
                    className="bar-row"
                    style={{ gridTemplateColumns: "minmax(0,1fr) 110px 80px" }}
                  >
                    <span className="ellipsis mono">{item.source}</span>
                    <div className="bar" style={{ position: "relative", overflow: "visible" }}>
                      <div
                        style={{ width: `${(item.p50 / scale) * 100}%`, background: "var(--fg2)" }}
                      />
                      <span
                        style={{
                          position: "absolute",
                          top: -3,
                          width: 2,
                          height: 11,
                          left: `${Math.min(98, (item.p95 / scale) * 100)}%`,
                          background: "var(--w)",
                        }}
                      />
                    </div>
                    <span className="mono fg2" style={{ textAlign: "right", fontSize: 12 }}>
                      {item.p50} / {item.p95}
                    </span>
                  </div>
                ))}
                <div className="muted">Bar p50 · tick p95 · ms</div>
              </div>
            </Card>
          </div>
        </div>
      )}
    </div>
  );
}
