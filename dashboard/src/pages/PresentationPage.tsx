import { useMemo } from "react";
import { ChatBubble, useAutoScroll } from "../components/session/Center";
import { Logo, StateBadge } from "../components/ui";
import { stopwatch, usd } from "../lib/format";
import { chat, elapsedMs, LIFECYCLE, matrix } from "../lib/model";
import { navigate, sessionPath } from "../state/route";
import { useSessionContext } from "../state/session";
import { useTheme } from "../state/theme";
import { useContract } from "../state/useContract";

const MARK = { pass: "✓", fail: "✕", running: "…", idle: "", na: "" };

export function PresentationPage() {
  useTheme("dark");
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
  const step = session ? Math.max(0, LIFECYCLE.indexOf(session.state)) : 0;
  const progress = session?.state === "CERTIFIED" ? 100 : (step / (LIFECYCLE.length - 1)) * 100;
  const caption =
    session?.state === "CERTIFIED"
      ? "Certified · every scenario green on both sides and end-to-end"
      : "Only the contract and test results cross the boundary";

  return (
    <div className="present" data-testid="presentation">
      <div className="present-top">
        <div className="row" style={{ gap: 14 }}>
          <Logo size={22} />
          <span style={{ fontSize: 22, fontWeight: 600 }}>DoubleBlind</span>
        </div>
        <div className="present-pair">
          <span style={{ color: "var(--a)" }}>{consumer?.display_name}</span>
          <span style={{ color: "var(--fg3)" }}>↔</span>
          <span style={{ color: "var(--b)" }}>{provider?.display_name}</span>
        </div>
        {session && <StateBadge state={session.state} large />}
        <span className="grow" />
        <div className="row" style={{ gap: 36, alignItems: "flex-end" }}>
          <div className="present-num">
            <div className="label">Elapsed</div>
            <div className="v">{stopwatch(elapsedMs(data.events, session?.state, now))}</div>
          </div>
          <div className="present-num">
            <div className="label">Cost</div>
            <div className="v">{usd(cost)}</div>
          </div>
        </div>
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
        <div className="present-card" style={{ flex: "1.2 1 560px", minHeight: 520 }}>
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
        <div className="present-card" style={{ flex: "1 1 520px" }}>
          <div className="head">
            <span>Scenarios</span>
            <span className="mono" style={{ color: "var(--o)" }}>
              {passing} / {rows.length}
            </span>
          </div>
          <div className="present-mrow label" style={{ padding: "14px 24px 6px", fontSize: 13 }}>
            <span />
            <span />
            <span style={{ textAlign: "center", color: "var(--a)" }}>
              {consumer?.display_name?.toUpperCase()}
            </span>
            <span style={{ textAlign: "center", color: "var(--b)" }}>
              {provider?.display_name?.toUpperCase()}
            </span>
            <span style={{ textAlign: "center" }}>E2E</span>
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
      <div className="present-foot">
        <div className="row" style={{ alignItems: "baseline", gap: 14 }}>
          <span className="present-zero">{metrics?.code_lines_shared ?? 0}</span>
          <span style={{ fontSize: 24, fontWeight: 500 }}>lines of code shared</span>
        </div>
        <div className="progress">
          <div style={{ width: `${progress}%` }} />
        </div>
        <span style={{ fontSize: 18, color: "var(--fg2)" }}>{caption}</span>
      </div>
    </div>
  );
}
