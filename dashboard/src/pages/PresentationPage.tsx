import { useMemo } from "react";
import { ChatBubble, useAutoScroll } from "../components/session/Center";
import { ReplayBar } from "../components/session/Header";
import { Logo, StateBadge } from "../components/ui";
import { stopwatch, usd } from "../lib/format";
import { chat, elapsedMs, matrix } from "../lib/model";
import { navigate, sessionPath } from "../state/route";
import { useSessionContext } from "../state/session";
import { useContract } from "../state/useContract";

const MARK = { pass: "✓", fail: "✗", running: "…", idle: "·", na: "—" };

export function PresentationPage() {
  const { id, token, data, now } = useSessionContext();
  const session = data.session;
  const items = useMemo(() => chat(data.events, session), [data.events, session]);
  const ref = useAutoScroll(items.length);
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
  const metrics = session?.metrics;
  const cost = metrics ? metrics.cost_usd + (metrics.sandbox_cost_usd ?? 0) : 0;
  const passing = rows.filter((row) => row.cells[row.side] === "pass").length;
  const certified = session?.state === "CERTIFIED";
  const caption =
    session?.state === "CERTIFIED"
      ? "Certified · every scenario green on both sides and end-to-end"
      : "Only the contract and test results cross the boundary";

  return (
    <div className="present" data-testid="presentation">
      <div className="cert-stripe" />
      <div className="present-top">
        <Logo size={30} />
        <span className="present-pair">
          {consumer?.display_name} ↔ {provider?.display_name}
        </span>
        {session && <StateBadge state={session.state} large />}
        <span className="grow" />
        <div className="row" style={{ gap: 36 }}>
          <div className="present-num">
            Elapsed
            <div className="v">{stopwatch(elapsedMs(data.events, session?.state, now))}</div>
          </div>
          <div className="present-num">
            Cost
            <div className="v">{usd(cost)}</div>
          </div>
        </div>
        <ReplayBar />
        <button
          type="button"
          className="btn btn-lg"
          style={{ fontSize: 15 }}
          onClick={() => navigate(sessionPath(id))}
        >
          Exit
        </button>
      </div>
      <div className="present-body">
        <div className="present-card" style={{ flex: "1.3 1 520px" }}>
          <div className="head">
            <span>Negotiation room</span>
            <span className="mono">
              round {session?.round ?? 0} / {session?.max_rounds ?? "—"}
            </span>
          </div>
          <div className="present-chat" ref={ref}>
            {items.map((item) => (
              <ChatBubble
                key={item.kind === "turn" ? item.turn.seq : item.seq}
                item={item}
                session={session}
              />
            ))}
          </div>
        </div>
        <div className="present-card" style={{ flex: "1 1 420px" }}>
          <div className="head">
            <span>Scenarios</span>
            <span className="mono" style={{ color: certified ? "var(--o)" : "var(--fg)" }}>
              {passing} / {rows.length}
            </span>
          </div>
          <div className="present-mrow present-cols">
            <span />
            <span />
            <span>{consumer?.display_name?.toUpperCase()}</span>
            <span>{provider?.display_name?.toUpperCase()}</span>
            <span>E2E</span>
          </div>
          <div className="present-matrix">
            {rows.map((row) => (
              <div key={row.id} className="present-mrow">
                <span className="id">{row.id}</span>
                <span className="title ellipsis">{row.title}</span>
                {(["consumer", "provider", "e2e"] as const).map((column) => (
                  <div key={column} className={`present-cell ${row.cells[column]}`}>
                    {MARK[row.cells[column]]}
                  </div>
                ))}
              </div>
            ))}
          </div>
        </div>
      </div>
      <footer className="present-foot" style={certified ? { color: "var(--o)" } : undefined}>
        {caption}
      </footer>
    </div>
  );
}
