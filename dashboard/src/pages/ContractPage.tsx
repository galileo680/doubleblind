import { useMemo, useState, type ReactNode } from "react";
import { DiffView } from "../components/session/Center";
import { Badge, Empty, Segmented, Skeleton, StateBadge, Swatch } from "../components/ui";
import type { ContractView } from "../lib/contract";
import { dateTime } from "../lib/format";
import { contractVersions, matrix, partyName } from "../lib/model";
import { navigate, sessionPath } from "../state/route";
import { useSessionContext } from "../state/session";
import { useContract, useContractDiff } from "../state/useContract";

function Section({
  id,
  title,
  note,
  children,
}: {
  id: string;
  title: string;
  note?: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="card">
      <div className="section-head">
        <span className="title">{title}</span>
        {note && <span className="muted">{note}</span>}
      </div>
      {children}
    </section>
  );
}

function Rendered({ contract }: { contract: ContractView }) {
  const { data } = useSessionContext();
  const session = data.session;
  const consumer =
    session?.parties.find((party) => party.role === "consumer")?.display_name ?? "consumer";
  const provider =
    session?.parties.find((party) => party.role === "provider")?.display_name ?? "provider";
  const rows = useMemo(
    () => matrix(contract.scenarios, data.events, session),
    [contract, data.events, session],
  );
  const opColumns = "170px 64px minmax(200px,1.3fr) 160px minmax(180px,1fr)";
  return (
    <>
      <Section
        id="operations"
        title="Operations"
        note={`Provided by ${provider} · called by ${consumer}`}
      >
        <div className="table-wrap">
          <div style={{ minWidth: 760 }}>
            <div className="trow head table-head" style={{ gridTemplateColumns: opColumns }}>
              <span>Operation</span>
              <span>Method</span>
              <span>Path</span>
              <span>Request</span>
              <span>Responses</span>
            </div>
            {contract.operations.map((operation) => (
              <div key={operation.id} className="trow" style={{ gridTemplateColumns: opColumns }}>
                <span>
                  <div style={{ fontWeight: 500 }}>{operation.id}</div>
                  {operation.headers.length > 0 && (
                    <div className="muted mono">{operation.headers.join(", ")}</div>
                  )}
                </span>
                <span className="method">{operation.method}</span>
                <span className="mono" style={{ overflowWrap: "anywhere" }}>
                  {operation.path}
                </span>
                <span className="mono fg2">{operation.requestBody}</span>
                <span className="mono fg2" style={{ overflowWrap: "anywhere" }}>
                  {operation.responses.join(" · ")}
                </span>
              </div>
            ))}
          </div>
        </div>
      </Section>
      <Section id="webhooks" title="Webhooks" note={`${provider} → ${consumer}`}>
        {contract.webhooks.length ? (
          <div className="hooks-grid">
            {contract.webhooks.map((webhook) => (
              <div key={webhook.id}>
                <div className="mono" style={{ fontWeight: 500 }}>
                  {webhook.id}
                </div>
                {webhook.description && (
                  <div className="fg2" style={{ fontSize: 12, marginTop: 4 }}>
                    {webhook.description}
                  </div>
                )}
                <div className="mono muted" style={{ marginTop: 6 }}>
                  {webhook.method} · payload {webhook.body}
                  {webhook.signature ? ` · ${webhook.signature}` : ""}
                </div>
              </div>
            ))}
          </div>
        ) : (
          <div className="muted" style={{ padding: 14 }}>
            No webhooks.
          </div>
        )}
      </Section>
      <section id="schemas" className="schema-grid">
        {contract.schemas.map((schema) => (
          <div key={schema.name} className="card">
            <div className="section-head" style={{ justifyContent: "space-between" }}>
              <span className="mono" style={{ fontWeight: 600 }}>
                {schema.name}
              </span>
              <span className="muted">schema</span>
            </div>
            {schema.fields.map((field) => (
              <div
                key={field.name}
                className="trow"
                style={{ gridTemplateColumns: "minmax(120px,1fr) 130px 1fr", padding: "7px 16px" }}
              >
                <span className="mono" style={{ overflowWrap: "anywhere" }}>
                  {field.name}
                  {field.required && <span style={{ color: "var(--e)" }}> *</span>}
                </span>
                <span className="mono" style={{ color: "var(--a)", overflowWrap: "anywhere" }}>
                  {field.type}
                </span>
                <span className="muted" style={{ overflowWrap: "anywhere" }}>
                  {field.note}
                </span>
              </div>
            ))}
            {schema.fields.length === 0 && (
              <div className="muted" style={{ padding: "8px 16px" }}>
                No declared properties.
              </div>
            )}
          </div>
        ))}
      </section>
      <Section
        id="mappings"
        title="Semantic mappings"
        note="Each side implements its half — neither sees the other's models"
      >
        {contract.mappings.length === 0 && (
          <div className="muted" style={{ padding: 14 }}>
            No mappings.
          </div>
        )}
        {contract.mappings.flatMap((mapping) =>
          mapping.pairs.map(([from, to]) => (
            <div
              key={`${mapping.name}-${from}`}
              className="trow"
              style={{
                gridTemplateColumns: "minmax(160px,1fr) minmax(160px,1fr) 24px minmax(160px,1fr)",
              }}
            >
              <span className="muted mono">{mapping.name}</span>
              <span className="mono" style={{ color: "var(--a)" }}>
                {from}
              </span>
              <span className="muted" style={{ textAlign: "center" }}>
                →
              </span>
              <span className="mono" style={{ color: "var(--b)" }}>
                {to}
              </span>
            </div>
          )),
        )}
      </Section>
      <Section id="decisions" title="Decisions">
        {contract.decisions.map((decision) => (
          <div
            key={decision.id}
            className="list-row"
            style={{ gridTemplateColumns: "48px 1fr auto" }}
          >
            <span className="id">{decision.id}</span>
            <div>
              <div>{decision.text}</div>
              <div className="muted">
                {decision.origin_question
                  ? `Human answer to ${decision.origin_question}`
                  : "Agreed by both agents"}
              </div>
            </div>
            <span className="row" style={{ gap: 6, fontSize: 12 }}>
              <Swatch tone={decision.owner === "provider" ? "b" : "a"} size={7} />
              {decision.owner === "provider" ? provider : consumer} implements
            </span>
          </div>
        ))}
      </Section>
      <Section
        id="scenarios"
        title="Test scenarios"
        note={`${contract.scenarios.length} scenarios`}
      >
        {rows.map((row) => {
          const status = row.cells[row.side];
          return (
            <div
              key={row.id}
              className="trow"
              style={{ gridTemplateColumns: "52px 1fr 130px 80px" }}
            >
              <span className="mono muted" style={{ fontWeight: 600 }}>
                {row.id}
              </span>
              <span>{row.title}</span>
              <span
                className={`run-tag tone-${row.side === "consumer" ? "a" : row.side === "provider" ? "b" : "n"}`}
                style={{ justifySelf: "start" }}
              >
                {row.side === "consumer"
                  ? consumer
                  : row.side === "provider"
                    ? provider
                    : "End-to-end"}
              </span>
              <span
                className="mono"
                style={{
                  textAlign: "right",
                  fontSize: 11.5,
                  color:
                    status === "pass" ? "var(--o)" : status === "fail" ? "var(--e)" : "var(--fg3)",
                }}
              >
                {status === "pass" ? "Pass" : status === "fail" ? "Fail" : "Not run"}
              </span>
            </div>
          );
        })}
      </Section>
    </>
  );
}

export function ContractPage() {
  const { id, token, data } = useSessionContext();
  const session = data.session;
  const versions = useMemo(() => contractVersions(data.events), [data.events]);
  const latest = session?.current_contract_version ?? versions.at(-1)?.version ?? 1;
  const signed = session?.signed_contract_version ?? 0;
  const [picked, setPicked] = useState<number | null>(null);
  const [mode, setMode] = useState<"rendered" | "diff">("rendered");
  const version = picked && picked <= latest ? picked : signed || latest;
  const { text, contract, error } = useContract(id, token, version, signed);
  const diff = useContractDiff(id, token, Math.max(1, version - 1), version);
  const consumer = session?.parties.find((party) => party.role === "consumer");
  const provider = session?.parties.find((party) => party.role === "provider");

  const download = () => {
    if (!text) return;
    const link = document.createElement("a");
    link.href = URL.createObjectURL(new Blob([text], { type: "application/yaml" }));
    link.download = `${contract?.id || id}-v${version}.yaml`;
    link.click();
    URL.revokeObjectURL(link.href);
  };

  const toc: [string, string, number][] = contract
    ? [
        ["operations", "Operations", contract.operations.length],
        ["webhooks", "Webhooks", contract.webhooks.length],
        ["schemas", "Schemas", contract.schemas.length],
        [
          "mappings",
          "Mappings",
          contract.mappings.reduce((sum, item) => sum + item.pairs.length, 0),
        ],
        ["decisions", "Decisions", contract.decisions.length],
        ["scenarios", "Scenarios", contract.scenarios.length],
      ]
    : [];

  return (
    <div className="page" data-testid="contract-page">
      <div className="page-head">
        <div className="grow" style={{ minWidth: 300 }}>
          <div className="eyebrow">
            <span>{contract?.id || id}</span>
            {session && <StateBadge state={session.state} />}
          </div>
          <h1>{session?.name ?? "Contract"}</h1>
          <div className="parties-line">
            <span className="row" style={{ gap: 6 }}>
              <Swatch tone="a" />
              {consumer?.display_name} · consumer
            </span>
            <span className="role">↔</span>
            <span className="row" style={{ gap: 6 }}>
              <Swatch tone="b" />
              {provider?.display_name} · provider
            </span>
            <span className="role">·</span>
            <span className="mono" style={{ fontSize: 12 }}>
              v{version} {contract?.status}
            </span>
          </div>
        </div>
        <div className="row wrap">
          <Segmented
            value={mode}
            onChange={setMode}
            options={[
              { value: "rendered", label: "Rendered" },
              {
                value: "diff",
                label: `Diff v${Math.max(1, version - 1)} → v${version}`,
                disabled: version <= 1,
              },
            ]}
          />
          <button type="button" className="btn" onClick={download} disabled={!text}>
            Download YAML
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => navigate(sessionPath(id, "certificate"))}
          >
            View certificate
          </button>
        </div>
      </div>
      {latest <= 1 ? (
        <Empty title="No contract draft yet" icon="doc">
          The provider's agent drafts the first version in round 1.
        </Empty>
      ) : (
        <div className="contract-layout">
          <nav className="toc">
            {toc.map(([anchor, label, count]) => (
              <a
                key={anchor}
                href={`#/s/${id}/contract`}
                onClick={(event) => (
                  event.preventDefault(),
                  document.getElementById(anchor)?.scrollIntoView({ behavior: "smooth" })
                )}
              >
                <span>{label}</span>
                <span className="mono muted">{count}</span>
              </a>
            ))}
          </nav>
          <div className="contract-main">
            {error && <div className="alert alert-error">{error}</div>}
            {!contract ? (
              <div className="card">
                <Skeleton />
              </div>
            ) : mode === "diff" ? (
              <div className="card">
                <div className="section-head">
                  <span className="title">Changes in v{version}</span>
                  <span className="muted">compared with v{Math.max(1, version - 1)}</span>
                </div>
                {diff.value !== null ? <DiffView text={diff.value} /> : <Skeleton />}
              </div>
            ) : (
              <Rendered contract={contract} />
            )}
          </div>
          <aside className="contract-side card">
            <div className="section-head">
              <span className="title" style={{ fontSize: 13 }}>
                Version history
              </span>
            </div>
            <div className="scroll" style={{ maxHeight: "70vh" }}>
              {[...versions].reverse().map((item) => (
                <div
                  key={item.version}
                  className={`version${item.version === version ? " on" : ""}${item.version === signed ? " signed" : ""}`}
                  onClick={() => setPicked(item.version)}
                >
                  <span className="ring" />
                  <div>
                    <div className="row" style={{ justifyContent: "space-between" }}>
                      <span className="mono" style={{ fontWeight: 600 }}>
                        v{item.version}
                      </span>
                      {item.version === signed && <Badge tone="o">signed</Badge>}
                    </div>
                    <div className="fg2" style={{ fontSize: 12 }}>
                      {item.version === 1
                        ? "Empty skeleton from the broker"
                        : item.actor.startsWith("human:")
                          ? `Decision from ${partyName(session, item.actor.slice(6))} humans`
                          : item.actor === "broker"
                            ? "Added by the broker"
                            : `Proposed by ${partyName(session, item.actor)} agent`}
                    </div>
                    <div className="muted">
                      {item.added || item.removed ? `+${item.added} −${item.removed} · ` : ""}
                      {dateTime(item.ts)}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </aside>
        </div>
      )}
    </div>
  );
}
