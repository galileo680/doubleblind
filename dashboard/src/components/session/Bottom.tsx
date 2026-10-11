import { useMemo, useState } from "react";
import { bytes, clock, usd } from "../../lib/format";
import {
  costs,
  matrix,
  ofType,
  partyName,
  timeline,
  type CellStatus,
  type TimelineKind,
} from "../../lib/model";
import { OBSERVER, useSessionContext } from "../../state/session";
import { useContract } from "../../state/useContract";
import { Card, CardHead, Swatch } from "../ui";

const CELL_LABEL: Record<CellStatus, string> = {
  pass: "Pass",
  fail: "Fail",
  running: "Running",
  idle: "—",
  na: "",
};

export function ScenarioMatrix() {
  const { id, token, data, view } = useSessionContext();
  const session = data.session;
  const version = session?.signed_contract_version ?? session?.current_contract_version ?? null;
  const { contract } = useContract(
    id,
    token,
    version && version > 1 ? version : null,
    session?.signed_contract_version ?? 0,
  );
  const rows = useMemo(
    () => matrix(contract?.scenarios ?? [], data.events, session),
    [contract, data.events, session],
  );
  const consumer = session?.parties.find((party) => party.role === "consumer");
  const provider = session?.parties.find((party) => party.role === "provider");
  const columns = (
    view === provider?.party ? ["provider", "consumer", "e2e"] : ["consumer", "provider", "e2e"]
  ) as ("consumer" | "provider" | "e2e")[];
  const label = {
    consumer: `${consumer?.display_name ?? "Consumer"} tests`,
    provider: `${provider?.display_name ?? "Provider"} tests`,
    e2e: "End-to-end",
  };
  const tone = { consumer: "a", provider: "b", e2e: "fg2" } as const;
  const state = session?.state;
  const note =
    state === "IMPLEMENTING"
      ? "Each side runs its own contract tests in its sandbox"
      : state === "E2E"
        ? "Real systems through the DoubleBlind gateway"
        : state === "CERTIFIED"
          ? "All green · certified"
          : "Runs after both sides implement";
  return (
    <div className="session-band">
      <Card testId="scenario-matrix">
        <div className="matrix-wrap">
          <div className="matrix">
            <CardHead title="Scenario matrix" note={note}>
              <div className="legend">
                <span>
                  <i style={{ background: "var(--s3)", border: "1px solid var(--line2)" }} />
                  Not run
                </span>
                <span>
                  <i style={{ background: "var(--i)" }} />
                  Running
                </span>
                <span>
                  <i style={{ background: "var(--e)" }} />
                  Failing
                </span>
                <span>
                  <i style={{ background: "var(--o)" }} />
                  Passing
                </span>
              </div>
            </CardHead>
            <div className="matrix-row head">
              <span />
              <span>Scenario</span>
              {columns.map((column) => (
                <span key={column}>
                  <Swatch tone={tone[column]} size={7} />
                  {label[column]}
                </span>
              ))}
            </div>
            {rows.length === 0 && (
              <div className="muted" style={{ padding: 14 }}>
                Scenarios appear as soon as the contract has them.
              </div>
            )}
            {rows.map((row) => (
              <div key={row.id} className="matrix-row" data-testid={`matrix-${row.id}`}>
                <span className="id">{row.id}</span>
                <span className="title">{row.title}</span>
                {columns.map((column) => {
                  const status = row.cells[column];
                  return (
                    <div key={column} className="cell-wrap">
                      <div className={`cell ${status}`}>
                        {status !== "na" && <span className="dot" />}
                        {CELL_LABEL[status]}
                      </div>
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        </div>
      </Card>
    </div>
  );
}

const FILTERS: ["all" | TimelineKind, string][] = [
  ["all", "All"],
  ["agent", "Agents"],
  ["human", "Humans"],
  ["test", "Tests"],
  ["gateway", "Gateway"],
  ["egress", "Egress"],
];

export function EventTimeline({ limit = 300 }: { limit?: number }) {
  const { data } = useSessionContext();
  const [filter, setFilter] = useState<"all" | TimelineKind>("all");
  const items = useMemo(() => timeline(data.events, data.session), [data.events, data.session]);
  const shown = items
    .filter((item) => filter === "all" || item.kind === filter)
    .slice(-limit)
    .reverse();
  return (
    <Card testId="event-timeline">
      <CardHead title="Event timeline">
        <div className="filters">
          {FILTERS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              className={`chip${filter === key ? " on" : ""}`}
              onClick={() => setFilter(key)}
            >
              {label}
            </button>
          ))}
        </div>
      </CardHead>
      <div className="scroll" style={{ maxHeight: 320 }}>
        {shown.length === 0 && (
          <div className="muted" style={{ padding: 14 }}>
            No events of this kind yet.
          </div>
        )}
        {shown.map((item) => (
          <div key={item.seq} className="event-row">
            <span className="t">{clock(item.ts)}</span>
            <span
              className="swatch"
              style={{ width: 7, height: 7, background: `var(--${item.tone})` }}
            />
            <div className="text">
              <span className="kind">{item.kind}</span>
              {item.text}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

export function EgressPanel() {
  const { data, view } = useSessionContext();
  const metrics = data.session?.metrics;
  const events = ofType(data.events, "egress.blocked", "egress.redacted").slice().reverse();
  const crossed = metrics ? metrics.bytes_brief + metrics.bytes_turns + metrics.bytes_results : 0;
  return (
    <Card testId="egress">
      <CardHead
        title="Egress guard"
        note={
          view === OBSERVER
            ? "Checks on everything that left either company"
            : "Checks on what left your company"
        }
      />
      <div className="stats3">
        <div>
          <div className="v" style={{ color: "var(--e)" }}>
            {metrics?.blocked_count ?? 0}
          </div>
          <div className="muted">Blocked</div>
        </div>
        <div>
          <div className="v" style={{ color: "var(--w)" }}>
            {metrics?.redacted_count ?? 0}
          </div>
          <div className="muted">Redacted</div>
        </div>
        <div>
          <div className="v">{bytes(crossed)}</div>
          <div className="muted">Crossed the boundary</div>
        </div>
      </div>
      <div className="scroll" style={{ maxHeight: 252 }}>
        {metrics && (
          <div className="facts" style={{ margin: 12 }}>
            <div>
              <span>Briefs</span>
              <span>{bytes(metrics.bytes_brief)}</span>
            </div>
            <div>
              <span>Negotiation turns</span>
              <span>{bytes(metrics.bytes_turns)}</span>
            </div>
            <div>
              <span>Test results</span>
              <span>{bytes(metrics.bytes_results)}</span>
            </div>
            <div>
              <span>Code lines shared</span>
              <span style={{ color: "var(--o)" }}>{metrics.code_lines_shared}</span>
            </div>
          </div>
        )}
        {events.map((event) => {
          const blocked = event.type === "egress.blocked";
          const reason = event.payload.reason;
          return (
            <div
              key={event.seq}
              className="stack"
              style={{ gap: 3, padding: "8px 14px", borderTop: "1px solid var(--line)" }}
            >
              <div className="row">
                <span className="mono muted">{clock(event.ts)}</span>
                <span className={`tag tone-${blocked ? "e" : "w"}`}>
                  {blocked ? "BLOCKED" : "REDACTED"}
                </span>
                <span className="muted">
                  {partyName(data.session, String(event.payload.party))} → shared
                </span>
                <span className="grow" />
                <span className="mono muted">{String(event.payload.kind ?? "")}</span>
              </div>
              <div className="fg2" style={{ fontSize: 12.5 }}>
                {Array.isArray(reason) ? reason.join("; ") : String(reason ?? "")}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}

function BarRows({ rows, tone }: { rows: [string, number][]; tone: string }) {
  const max = Math.max(...rows.map(([, value]) => value), 0.0001);
  return (
    <>
      {rows.map(([key, value]) => (
        <div key={key} className="bar-row">
          <span className="ellipsis">{key}</span>
          <div className="bar">
            <div style={{ width: `${(value / max) * 100}%`, background: tone }} />
          </div>
          <span className="mono" style={{ textAlign: "right" }}>
            {usd(value, value < 0.1 ? 4 : 2)}
          </span>
        </div>
      ))}
    </>
  );
}

export function CostPanel() {
  const { data, view } = useSessionContext();
  const metrics = data.session?.metrics;
  const breakdown = useMemo(() => costs(data.events), [data.events]);
  const total = metrics ? metrics.cost_usd + (metrics.sandbox_cost_usd ?? 0) : 0;
  const observer = view === OBSERVER;
  const sorted = (map: Map<string, number>) => [...map.entries()].sort((a, b) => b[1] - a[1]);
  const sandboxRows = sorted(breakdown.sandboxes).map(
    ([party, value]) =>
      [
        party === "e2e" ? "end-to-end · gateway" : `${partyName(data.session, party)} sandboxes`,
        value,
      ] as [string, number],
  );
  return (
    <Card testId="cost">
      <CardHead title="Cost" note="Session total">
        <span className="mono" style={{ fontWeight: 600 }}>
          {usd(total)}
        </span>
      </CardHead>
      <div className="card-pad" style={{ gap: 14 }}>
        <div className="facts">
          <div>
            <span>Models</span>
            <span>{usd(metrics?.cost_usd ?? 0)}</span>
          </div>
          <div>
            <span>Sandboxes</span>
            <span>{usd(metrics?.sandbox_cost_usd ?? 0)}</span>
          </div>
          <div>
            <span>Tokens</span>
            <span>{(metrics?.tokens ?? 0).toLocaleString()}</span>
          </div>
        </div>
        {observer ? (
          <>
            <div className="stack" style={{ gap: 7 }}>
              <span className="label">By model</span>
              {breakdown.models.size ? (
                <BarRows rows={sorted(breakdown.models)} tone="var(--a)" />
              ) : (
                <span className="muted">No model calls yet.</span>
              )}
            </div>
            <div className="stack" style={{ gap: 7 }}>
              <span className="label">By sandbox</span>
              {sandboxRows.length ? (
                <BarRows rows={sandboxRows} tone="var(--fg3)" />
              ) : (
                <span className="muted">No paid sandbox runs (local or Docker).</span>
              )}
            </div>
          </>
        ) : (
          <span className="muted">The per-model breakdown is visible in the Observer view.</span>
        )}
      </div>
    </Card>
  );
}
