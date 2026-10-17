import { useEffect, useMemo, useRef, useState } from "react";
import { api } from "../../api/client";
import type { SessionView } from "../../api/types";
import type { ContractView } from "../../lib/contract";
import {
  chat,
  contractVersions,
  partyOf,
  roleTone,
  signedParties,
  type ChatItem,
} from "../../lib/model";
import { useSessionContext } from "../../state/session";
import { useContract, useContractDiff } from "../../state/useContract";
import { Badge, Card, CardHead, Empty, Skeleton, Swatch } from "../ui";

export function ChatBubble({ item, session }: { item: ChatItem; session: SessionView | null }) {
  if (item.kind === "system") {
    return <div className={`system-msg tone-${item.tone}`}>{item.text}</div>;
  }
  const party = partyOf(session, item.turn.party);
  const tone = roleTone(party?.role);
  const side = party?.role === "provider" ? "right" : "left";
  return (
    <div className={`bubble-wrap ${side}`}>
      <div className="bubble-meta">
        <Swatch tone={tone} size={7} />
        <span className="who">{party?.display_name ?? item.turn.party} agent</span>
        <span className="mono">R{item.turn.round}</span>
        <span className="mono">→ v{item.turn.version}</span>
        {item.turn.status === "ready_to_sign" && <Badge tone="o">ready to sign</Badge>}
      </div>
      <div className={`bubble tone-${tone}`}>{item.turn.message}</div>
      {item.turn.patches.length > 0 && (
        <div className="bubble-patches">
          {item.turn.patches.length} change{item.turn.patches.length === 1 ? "" : "s"} ·{" "}
          {item.turn.patches.slice(0, 3).join(" · ")}
          {item.turn.patches.length > 3 ? " …" : ""}
        </div>
      )}
    </div>
  );
}

function typingText(session: SessionView | null, openFor: (party: string) => string[]): string {
  if (!session) return "";
  if (session.state === "BRIEFING")
    return "Both agents are reading their own company's code and docs…";
  if (session.state !== "NEGOTIATING" && session.state !== "AMENDING") return "";
  const party = partyOf(session, session.turn_party);
  if (!party) return "";
  const open = session.parties.flatMap((item) => openFor(item.party));
  if (open.length) return `Waiting for a human answer to ${open.join(", ")}`;
  return `${party.display_name} agent is drafting round ${session.round + 1}…`;
}

export function useAutoScroll(dependency: unknown) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const element = ref.current;
    if (element) element.scrollTop = element.scrollHeight;
  }, [dependency]);
  return ref;
}

export function NegotiationRoom() {
  const { data } = useSessionContext();
  const session = data.session;
  const items = useMemo(() => chat(data.events, session), [data.events, session]);
  const typing = typingText(session, (party) =>
    data.questions
      .filter((question) => question.party === party && !question.answer)
      .map((question) => question.id),
  );
  const ref = useAutoScroll(items.length + typing);
  const turnsCount = items.filter((item) => item.kind === "turn").length;
  return (
    <Card testId="negotiation-room">
      <CardHead title="Negotiation room" note={`Shared with both companies · ${turnsCount} turns`}>
        <span className="mono muted">
          round {session?.round ?? 0} / {session?.max_rounds ?? "—"}
        </span>
      </CardHead>
      <div className="chat" ref={ref}>
        {items.length === 0 && (
          <div className="muted">The first turn appears when both briefs are shared.</div>
        )}
        {items.map((item) => (
          <ChatBubble
            key={item.kind === "turn" ? item.turn.seq : item.seq}
            item={item}
            session={session}
          />
        ))}
        {typing && (
          <div className="typing">
            <span className="typing-dots">
              <span />
              <span />
              <span />
            </span>
            {typing}
          </div>
        )}
      </div>
    </Card>
  );
}

function YamlView({ text }: { text: string }) {
  const lines = text.replace(/\n$/, "").split("\n");
  return (
    <div className="code" style={{ maxHeight: 420 }}>
      {lines.map((line, index) => {
        const comment = line.indexOf(" #");
        const body = comment > 0 ? line.slice(0, comment) : line;
        const match = body.match(/^(\s*-?\s*[^:'"{[]+:)(.*)$/);
        return (
          <div key={index} className="code-line">
            <span className="ln">{index + 1}</span>
            <span className="key">{match ? match[1] : body}</span>
            <span className="rest">{match ? match[2] : ""}</span>
            <span className="com">{comment > 0 ? line.slice(comment) : ""}</span>
          </div>
        );
      })}
    </div>
  );
}

export function DiffView({ text }: { text: string }) {
  const lines = text
    .split("\n")
    .filter((line) => !line.startsWith("---") && !line.startsWith("+++"));
  if (!lines.some((line) => line.trim()))
    return (
      <div className="muted" style={{ padding: 14 }}>
        No changes.
      </div>
    );
  return (
    <div className="code" style={{ padding: "8px 0" }}>
      {lines.map((line, index) => {
        const sign = line[0];
        const kind = line.startsWith("@@")
          ? "hunk"
          : sign === "+"
            ? "add"
            : sign === "-"
              ? "del"
              : "";
        return (
          <div key={index} className={`diff-line ${kind}`}>
            <span className="sign">{kind === "hunk" ? "" : sign === " " ? "" : sign}</span>
            {kind === "hunk" ? line : line.slice(1)}
          </div>
        );
      })}
    </div>
  );
}

export function DecisionsList({ contract, signed }: { contract: ContractView; signed: boolean }) {
  if (!contract.decisions.length)
    return (
      <div className="muted" style={{ padding: 14 }}>
        No decisions yet.
      </div>
    );
  return (
    <>
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
              Owner: {decision.owner}
              {decision.origin_question ? ` · answer to ${decision.origin_question}` : ""}
            </div>
          </div>
          <Badge tone={signed ? "o" : "w"} dot={false}>
            {signed ? "Agreed" : "Proposed"}
          </Badge>
        </div>
      ))}
    </>
  );
}

const SIDE_TONE = { consumer: "a", provider: "b", e2e: "n" } as const;

export function ScenarioList({
  contract,
  session,
}: {
  contract: ContractView;
  session: SessionView | null;
}) {
  const label = (side: string) =>
    side === "e2e"
      ? "End-to-end"
      : (session?.parties.find((party) => party.role === side)?.display_name ?? side);
  return (
    <>
      {contract.scenarios.map((scenario) => (
        <div
          key={scenario.id}
          className="list-row"
          style={{ gridTemplateColumns: "48px 1fr auto", alignItems: "center" }}
        >
          <span className="id">{scenario.id}</span>
          <div>
            <div>{scenario.title}</div>
            {scenario.covers.length > 0 && (
              <div className="muted mono">covers {scenario.covers.join(", ")}</div>
            )}
          </div>
          <span className={`run-tag tone-${SIDE_TONE[scenario.side]}`}>{label(scenario.side)}</span>
        </div>
      ))}
    </>
  );
}

type Tab = "contract" | "diff" | "decisions" | "scenarios";

export function ContractTabs() {
  const { id, token, data } = useSessionContext();
  const session = data.session;
  const versions = useMemo(() => contractVersions(data.events), [data.events]);
  const latest = session?.current_contract_version ?? versions.at(-1)?.version ?? 1;
  const [tab, setTab] = useState<Tab>("contract");
  const [picked, setPicked] = useState<number | null>(null);
  const version = picked && picked <= latest ? picked : latest;
  const signed = session?.signed_contract_version ?? 0;
  const { text, contract, error } = useContract(id, token, version, signed);
  const diff = useContractDiff(id, token, Math.max(1, version - 1), version);
  const empty = latest <= 1;

  return (
    <Card testId="contract-tabs">
      <div className="tabs">
        {(
          [
            ["contract", "Contract", ""],
            ["diff", "Diff", ""],
            ["decisions", "Decisions", contract?.decisions.length || ""],
            ["scenarios", "Scenarios", contract?.scenarios.length || ""],
          ] as [Tab, string, number | string][]
        ).map(([key, label, count]) => (
          <button
            key={key}
            type="button"
            className={`tab${tab === key ? " on" : ""}`}
            onClick={() => setTab(key)}
          >
            {label}
            {count !== "" && <span className="n">{count}</span>}
          </button>
        ))}
        <span className="grow" />
        {!empty && (
          <select
            className="select"
            value={version}
            onChange={(event) => setPicked(Number(event.target.value))}
            aria-label="Contract version"
          >
            {versions.map((item) => (
              <option key={item.version} value={item.version}>
                v{item.version}
                {item.version === signed ? " · signed" : ""}
              </option>
            ))}
          </select>
        )}
      </div>
      {empty ? (
        <Empty title="No contract draft yet" icon="doc">
          Both agents are reading their own company's code and docs. The first draft appears after
          round 1.
        </Empty>
      ) : error ? (
        <div className="alert alert-error" style={{ margin: 14 }}>
          {error}
        </div>
      ) : !text || !contract ? (
        <Skeleton />
      ) : tab === "contract" ? (
        <YamlView text={text} />
      ) : tab === "diff" ? (
        <>
          <div
            className="muted"
            style={{ padding: "8px 14px", borderBottom: "1px solid var(--line)" }}
          >
            v{Math.max(1, version - 1)} → v{version}
          </div>
          {diff.value !== null ? (
            <DiffView text={diff.value} />
          ) : (
            <Skeleton widths={["60%", "80%"]} />
          )}
        </>
      ) : tab === "decisions" ? (
        <DecisionsList contract={contract} signed={version === signed} />
      ) : (
        <ScenarioList contract={contract} session={session} />
      )}
    </Card>
  );
}

export function SignaturesBar() {
  const { id, token, view, data, replay } = useSessionContext();
  const session = data.session;
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  if (!session) return null;
  const awaiting = session.state === "AWAITING_SIGNATURES";
  const version = awaiting
    ? session.current_contract_version
    : (session.signed_contract_version ?? session.current_contract_version);
  const signed = signedParties(data.events, version);
  const opened = awaiting || session.signed_contract_version !== null;
  const sign = async () => {
    setBusy(true);
    setError(null);
    try {
      await api.sign(id, token, version);
      data.refresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Card testId="signatures">
      <div className="signatures">
        <div style={{ minWidth: 150 }}>
          <div style={{ fontWeight: 600 }}>Signatures</div>
          <div className="muted">
            {opened ? `Contract v${version}` : "Open when the contract converges"}
          </div>
        </div>
        {session.parties.map((party) => {
          const done =
            signed.has(party.party) || (!awaiting && session.signed_contract_version === version);
          const canSign = awaiting && !done && view === party.party && !replay.recorded;
          return (
            <div key={party.party} className={`sig${canSign ? " need" : ""}`}>
              <Swatch tone={roleTone(party.role)} />
              <div className="grow" style={{ minWidth: 0, lineHeight: 1.35 }}>
                <div style={{ fontWeight: 500 }}>{party.display_name}</div>
                <div className="muted">{party.display_name} approver</div>
              </div>
              {canSign ? (
                <button
                  type="button"
                  className="btn btn-primary btn-sm"
                  onClick={sign}
                  disabled={busy}
                >
                  {busy && <span className="spinner" />}
                  Sign v{version}
                </button>
              ) : done && opened ? (
                <span className="signed">✓ Signed</span>
              ) : (
                <span className="pending">{opened ? "Pending" : "Not yet open"}</span>
              )}
            </div>
          );
        })}
      </div>
      {error && (
        <div className="alert alert-error" style={{ margin: "0 14px 12px" }}>
          {error}
        </div>
      )}
    </Card>
  );
}
