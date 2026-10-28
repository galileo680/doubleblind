import { useMemo, useState } from "react";
import { bytes, clock, usd } from "../../lib/format";
import {
  costs,
  matrix,
  ROLE_TASK,
  type ModelUse,
  ofType,
  partyName,
  timeline,
  type CellStatus,
  type TimelineKind,
} from "../../lib/model";
import { OBSERVER, useSessionContext } from "../../state/session";
import { useContract } from "../../state/useContract";
import { Card, CardHead, Segmented, Swatch } from "../ui";

const CELL_LABEL: Record<CellStatus, string> = {
  pass: "✓ pass",
  fail: "✗ fail",
  running: "running",
  idle: "·",
  na: "—",
};

type Column = "consumer" | "provider" | "e2e";

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
  const columns: Column[] =
    view === provider?.party ? ["provider", "consumer", "e2e"] : ["consumer", "provider", "e2e"];
  const heading = {
    consumer: [consumer?.display_name ?? "Consumer", "consumer contract tests"],
    provider: [provider?.display_name ?? "Provider", "provider contract tests"],
    e2e: ["End-to-end", "real systems via gateway"],
  };
  const count = (column: Column) => {
    const cells = rows.map((row) => row.cells[column]).filter((cell) => cell !== "na");
    return `${cells.filter((cell) => cell === "pass").length}/${cells.length}`;
  };
  const total = rows.flatMap((row) => columns.map((column) => row.cells[column]));
  const runnable = total.filter((cell) => cell !== "na");
  const green = runnable.filter((cell) => cell === "pass").length;
  const certified = session?.state === "CERTIFIED";
  const started = total.some((cell) => cell !== "idle" && cell !== "na");
  return (
    <Card testId="scenario-matrix">
      <CardHead title="Scenario matrix">
        <span
          className="mono"
          style={{ fontSize: 12, color: certified ? "var(--o)" : "var(--fg3)" }}
        >
          {certified
            ? "All green · certified"
            : started
              ? `${green}/${runnable.length} green`
              : "Each side runs its own contract tests in its sandbox"}
        </span>
      </CardHead>
      <div className="matrix-wrap">
        <div className="matrix">
          <div className="matrix-row head">
            <span style={{ paddingLeft: 14 }}>ID</span>
            <span>Scenario</span>
            {columns.map((column) => (
              <span key={column} className="matrix-col">
                <b>
                  {heading[column][0]} <span className="mono">{count(column)}</span>
                </b>
                {heading[column][1]}
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
              {columns.map((column) => (
                <div key={column} className={`cell ${row.cells[column]}`}>
                  {CELL_LABEL[row.cells[column]]}
                </div>
              ))}
            </div>
          ))}
        </div>
      </div>
    </Card>
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
      <div className="scroll" style={{ maxHeight: 520 }}>
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
              style={{ width: 8, height: 8, background: `var(--${item.tone})` }}
            />
            <div className="text">{item.text}</div>
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
  const partner = data.session?.parties.find((party) => party.party !== view);
  return (
    <Card testId="egress">
      <div className="card-head" style={{ display: "block" }}>
        <div className="card-title">Egress guard</div>
        <div className="muted" style={{ fontSize: 12.5 }}>
          {view === OBSERVER
            ? "Checks on everything that left either company"
            : "Checks on what left your company"}
        </div>
      </div>
      <div className="kpis flat">
        <div className="kpi">
          Briefs
          <div className="kpi-value small">{bytes(metrics?.bytes_brief ?? 0)}</div>
        </div>
        <div className="kpi">
          Negotiation turns
          <div className="kpi-value small">{bytes(metrics?.bytes_turns ?? 0)}</div>
        </div>
        <div className="kpi">
          Test results
          <div className="kpi-value small">{bytes(metrics?.bytes_results ?? 0)}</div>
        </div>
        <div className="kpi">
          Blocked
          <div className="kpi-value small" style={{ color: "var(--e)" }}>
            {metrics?.blocked_count ?? 0}
          </div>
        </div>
        <div className="kpi">
          Redacted
          <div className="kpi-value small" style={{ color: "var(--w)" }}>
            {metrics?.redacted_count ?? 0}
          </div>
        </div>
      </div>
      {events.map((event) => {
        const blocked = event.type === "egress.blocked";
        const reason = event.payload.reason;
        return (
          <div key={event.seq} className="guard-row">
            <span className={`badge tone-${blocked ? "e" : "w"}`}>
              {blocked ? "Blocked" : "Redacted"}
            </span>
            <span className="fg2" style={{ width: 70, flex: "none", fontSize: 12.5 }}>
              {partyName(data.session, String(event.payload.party))}
            </span>
            <span style={{ flex: "1 1 220px" }}>
              {Array.isArray(reason) ? reason.join("; ") : String(reason ?? "")}
            </span>
            <span className="mono muted">{clock(event.ts)}</span>
          </div>
        );
      })}
      {view !== OBSERVER && partner && (
        <div className="muted" style={{ padding: "10px 16px", fontSize: 12.5 }}>
          Details of {partner.display_name}'s blocked and redacted messages stay with{" "}
          {partner.display_name}; only the counts are shared.
        </div>
      )}
    </Card>
  );
}

function ModelRows({ models }: { models: Map<string, ModelUse> }) {
  const rows = [...models.entries()].sort((a, b) => b[1].cost - a[1].cost);
  const max = Math.max(...rows.map(([, use]) => use.cost), 0.0001);
  return (
    <>
      {rows.map(([name, use]) => (
        <div key={name} className="stack" style={{ gap: 4 }}>
          <div className="row wrap" style={{ gap: 10, alignItems: "baseline" }}>
            <span className="mono" style={{ flex: "1 1 200px", fontSize: 12.5, fontWeight: 500 }}>
              {name}
            </span>
            <span className="mono muted">{use.calls} calls</span>
            <span className="mono" style={{ width: 64, textAlign: "right", fontWeight: 600 }}>
              {usd(use.cost, use.cost < 0.01 ? 3 : 2)}
            </span>
          </div>
          <div className="bar thick">
            <div style={{ width: `${Math.max(1.5, (use.cost / max) * 100)}%` }} />
          </div>
          <span className="fg2" style={{ fontSize: 12 }}>
            {[...use.roles.entries()]
              .sort((a, b) => b[1] - a[1])
              .map(([role]) => ROLE_TASK[role] ?? role)
              .join(" · ")}
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
  const sandboxes = [...breakdown.sandboxes.entries()].sort((a, b) => b[1] - a[1]);
  const tone = (party: string) =>
    data.session?.parties.find((item) => item.party === party)?.role === "provider" ? "b" : "a";
  return (
    <Card testId="cost" className="cost-card">
      <div className="card-head" style={{ alignItems: "baseline" }}>
        <span className="card-title grow">Cost · this run</span>
        <span className="mono" style={{ fontWeight: 600, fontSize: 22 }}>
          {usd(total)}
        </span>
      </div>
      <div className="kpis flat" style={{ gridTemplateColumns: "repeat(3, 1fr)" }}>
        <div className="kpi">
          Models
          <div className="kpi-value small">{usd(metrics?.cost_usd ?? 0)}</div>
        </div>
        <div className="kpi">
          Sandboxes
          <div className="kpi-value small">{usd(metrics?.sandbox_cost_usd ?? 0)}</div>
        </div>
        <div className="kpi">
          Tokens
          <div className="kpi-value small">{(metrics?.tokens ?? 0).toLocaleString()}</div>
        </div>
      </div>
      {view === OBSERVER ? (
        <div className="card-pad" style={{ gap: 12, padding: "14px 16px" }}>
          <span className="panel-title">Which Nemotron does what</span>
          {breakdown.models.size ? (
            <ModelRows models={breakdown.models} />
          ) : (
            <span className="muted">No model calls yet.</span>
          )}
          <span className="panel-title cost-split">By sandbox</span>
          {sandboxes.length ? (
            sandboxes.map(([party, value]) => (
              <div key={party} className="row" style={{ gap: 10, fontSize: 13 }}>
                {party === "e2e" ? (
                  <Swatch tone="n" size={8} />
                ) : (
                  <Swatch tone={tone(party)} size={8} />
                )}
                <span className="grow">
                  {party === "e2e"
                    ? "End-to-end · gateway"
                    : `${partyName(data.session, party)} · Token Factory sandbox`}
                </span>
                <span className="mono" style={{ fontWeight: 600 }}>
                  {usd(value, value < 0.1 ? 3 : 2)}
                </span>
              </div>
            ))
          ) : (
            <span className="muted">No paid sandbox runs (local or Docker).</span>
          )}
        </div>
      ) : (
        <div className="muted" style={{ padding: "12px 16px", fontSize: 12.5 }}>
          The per-model breakdown is visible in the Observer view.
        </div>
      )}
    </Card>
  );
}

const PANELS = [
  ["scenarios", "Scenarios"],
  ["timeline", "Timeline"],
  ["egress", "Egress guard"],
  ["cost", "Cost"],
] as const;

export function BottomPanels() {
  const [panel, setPanel] = useState<(typeof PANELS)[number][0]>("scenarios");
  return (
    <div className="session-bottom">
      <Segmented
        value={panel}
        onChange={setPanel}
        options={PANELS.map(([value, label]) => ({ value, label }))}
      />
      {panel === "scenarios" ? (
        <ScenarioMatrix />
      ) : panel === "timeline" ? (
        <EventTimeline />
      ) : panel === "egress" ? (
        <EgressPanel />
      ) : (
        <CostPanel />
      )}
    </div>
  );
}
