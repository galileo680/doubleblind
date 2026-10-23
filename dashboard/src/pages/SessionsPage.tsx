import { useEffect, useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { PublicSession, SessionState, SessionView } from "../api/types";
import { Badge, Card, CardHead, Empty, Kpi, StateBadge, Swatch } from "../components/ui";
import { plural, usd } from "../lib/format";
import { roleTone } from "../lib/model";
import { navigate, sessionPath } from "../state/route";
import {
  forgetSession,
  fromTokens,
  saveSession,
  useStoredSessions,
  type StoredSession,
} from "../state/sessions";

type Group = "all" | "active" | "attention" | "certified" | "failed";

const GROUPS: [Group, string, string][] = [
  ["all", "All", "var(--fg3)"],
  ["active", "In progress", "var(--i)"],
  ["attention", "Needs attention", "var(--w)"],
  ["certified", "Certified", "var(--o)"],
  ["failed", "Failed", "var(--e)"],
];

const GROUP_STATES: Record<Exclude<Group, "all">, SessionState[]> = {
  active: ["CREATED", "BRIEFING", "NEGOTIATING", "IMPLEMENTING", "E2E"],
  attention: ["AWAITING_SIGNATURES", "ESCALATED", "AMENDING"],
  certified: ["CERTIFIED"],
  failed: ["FAILED"],
};

const PUBLIC_COLUMNS = "minmax(220px,1fr) 170px 230px";
const COLUMNS = "minmax(220px,2fr) minmax(180px,1.4fr) 170px 90px 80px 90px 110px 70px";

function tokenOf(session: StoredSession): string | null {
  return session.observer ?? Object.values(session.humans)[0] ?? null;
}

function inGroup(group: Group, view: SessionView | undefined): boolean {
  if (group === "all") return true;
  if (!view) return false;
  if (group === "attention" && view.open_questions > 0) return true;
  return GROUP_STATES[group].includes(view.state);
}

function useSessionViews(sessions: StoredSession[]) {
  const [views, setViews] = useState<Record<string, SessionView | string>>({});
  useEffect(() => {
    let cancelled = false;
    const refresh = () =>
      sessions.forEach((session) => {
        const token = tokenOf(session);
        if (!token) return;
        api
          .session(session.id, token)
          .then((view) => !cancelled && setViews((current) => ({ ...current, [session.id]: view })))
          .catch(
            (failure: unknown) =>
              !cancelled &&
              setViews((current) => ({
                ...current,
                [session.id]: String(failure instanceof Error ? failure.message : failure),
              })),
          );
      });
    refresh();
    const timer = window.setInterval(refresh, 4000);
    return () => {
      cancelled = true;
      window.clearInterval(timer);
    };
  }, [sessions]);
  return views;
}

function NewSession() {
  const [name, setName] = useState("Spokes ↔ Parcelly shipping");
  const [preset, setPreset] = useState("spokes-parcelly");
  const [admin, setAdmin] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const created = await api.createSession(name.trim(), preset.trim(), admin.trim() || null);
      const stored = fromTokens(created.session_id, name.trim(), created.tokens);
      saveSession(stored);
      if (stored.observer) await api.start(created.session_id, stored.observer);
      navigate(sessionPath(created.session_id));
    } catch (failure) {
      setError(failure instanceof Error ? failure.message : String(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="form-card" onSubmit={submit} data-testid="new-session">
      <div className="form-step">
        <span className="n">01</span>
        <b>New session</b>
        <span className="muted">Both agents start once they connect with their own tokens.</span>
      </div>
      <label className="field">
        Name, in one sentence
        <input
          className="input"
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          aria-label="Session name"
        />
      </label>
      <div className="row wrap">
        <label className="field grow">
          Preset (companies, roles, synthetic data)
          <input
            className="input mono"
            value={preset}
            onChange={(event) => setPreset(event.target.value)}
            required
          />
        </label>
        <label className="field grow">
          Admin token (only if the broker requires one)
          <input
            className="input mono"
            type="password"
            value={admin}
            onChange={(event) => setAdmin(event.target.value)}
            autoComplete="off"
          />
        </label>
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      <div className="row">
        <span className="muted grow">
          Tokens stay in this browser. Share a session with the link on its page.
        </span>
        <button className="btn btn-primary" type="submit" disabled={busy || !name.trim()}>
          {busy ? <span className="spinner" /> : <span>+</span>}
          Create and start
        </button>
      </div>
    </form>
  );
}

function PublicSessions() {
  const [sessions, setSessions] = useState<PublicSession[]>([]);
  useEffect(() => {
    api
      .publicSessions()
      .then(setSessions)
      .catch(() => setSessions([]));
  }, []);
  if (sessions.length === 0) return null;
  const open = (session: PublicSession, replay: boolean) => {
    saveSession(fromTokens(session.session_id, session.name, { observer: session.token }));
    navigate(`${sessionPath(session.session_id)}${replay ? "?replay=1" : ""}`);
  };
  return (
    <Card testId="public-sessions">
      <CardHead title="Public sessions" note="Recorded runs anyone can watch — no tokens needed" />
      {sessions.map((session) => (
        <div
          key={session.session_id}
          className="trow"
          style={{ gridTemplateColumns: PUBLIC_COLUMNS }}
        >
          <div className="ellipsis">
            <div style={{ fontWeight: 500 }} className="ellipsis">
              {session.name}
            </div>
            <div className="muted mono">{session.parties.join(" ↔ ")}</div>
          </div>
          <span>
            <StateBadge state={session.state} />
          </span>
          <span className="row" style={{ justifyContent: "flex-end" }}>
            <button
              type="button"
              className="btn btn-primary btn-sm"
              onClick={() => open(session, true)}
            >
              ▶ Watch replay
            </button>
            <button type="button" className="btn btn-sm" onClick={() => open(session, false)}>
              Open
            </button>
          </span>
        </div>
      ))}
    </Card>
  );
}

function OpenLink() {
  const [link, setLink] = useState("");
  const [error, setError] = useState<string | null>(null);
  const open = (event: FormEvent) => {
    event.preventDefault();
    const hash = link.includes("#") ? link.slice(link.indexOf("#")) : "";
    if (!hash.startsWith("#/s/")) {
      setError("Paste a session link that contains #/s/…");
      return;
    }
    setError(null);
    navigate(hash);
  };
  return (
    <form className="form-card" onSubmit={open}>
      <div className="form-step">
        <span className="n">02</span>
        <b>Open a shared session</b>
      </div>
      <div className="row">
        <input
          className="input mono grow"
          placeholder="http://…/#/s/ses_…?observer=…"
          value={link}
          onChange={(event) => setLink(event.target.value)}
          aria-label="Session link"
        />
        <button className="btn" type="submit" disabled={!link.trim()}>
          Open
        </button>
      </div>
      {error && <span className="field-error">{error}</span>}
    </form>
  );
}

export function SessionsPage() {
  const sessions = useStoredSessions();
  const views = useSessionViews(sessions);
  const [group, setGroup] = useState<Group>("all");
  const [query, setQuery] = useState("");
  const loaded = (id: string) =>
    typeof views[id] === "object" ? (views[id] as SessionView) : undefined;
  const rows = sessions.filter(
    (session) =>
      inGroup(group, loaded(session.id)) &&
      (!query || `${session.name} ${session.id}`.toLowerCase().includes(query.toLowerCase())),
  );
  const all = sessions.map((session) => loaded(session.id));
  const spend = all.reduce(
    (sum, view) => sum + (view ? view.metrics.cost_usd + (view.metrics.sandbox_cost_usd ?? 0) : 0),
    0,
  );
  const count = (key: Group) =>
    sessions.filter((session) => inGroup(key, loaded(session.id))).length;

  return (
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Sessions</h1>
          <div className="row wrap fg2">
            <span>{plural(sessions.length, "session")} in this browser</span>
            <span style={{ color: "var(--line2)" }}>·</span>
            <span className="row mono" style={{ color: "var(--o)", fontSize: 12 }}>
              <span className="dot" />0 lines of code shared, across all of them
            </span>
          </div>
        </div>
      </div>
      <PublicSessions />
      <div className="kpi-cards">
        <Kpi label="In progress" value={count("active")} sub="sessions" />
        <Kpi
          label="Needs attention"
          value={count("attention")}
          tone="w"
          sub="questions · signatures"
        />
        <Kpi label="Certified" value={count("certified")} tone="o" sub="all scenarios green" />
        <Kpi label="Spend" value={usd(spend)} sub="models + sandboxes" />
      </div>
      <div className="row wrap">
        <div className="filters">
          {GROUPS.map(([key, label, dot]) => (
            <button
              key={key}
              type="button"
              className={`chip chip-lg${group === key ? " on" : ""}`}
              onClick={() => setGroup(key)}
            >
              <span className="dot" style={{ background: dot }} />
              {label}
              <span className="mono muted">{count(key)}</span>
            </button>
          ))}
        </div>
        <span className="grow" />
        <input
          className="input"
          style={{ width: 240, height: 30 }}
          placeholder="Filter by name or id"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      {sessions.length === 0 ? (
        <Empty title="No sessions yet">
          Create a session below, or run <span className="mono">scripts/agent_session.py</span> — it
          prints a link that opens here.
        </Empty>
      ) : rows.length === 0 ? (
        <Empty
          title="No sessions match these filters"
          action={
            <button
              type="button"
              className="btn btn-sm"
              onClick={() => (setGroup("all"), setQuery(""))}
            >
              Clear filters
            </button>
          }
        />
      ) : (
        <Card>
          <div className="table-wrap">
            <div style={{ minWidth: 1040 }}>
              <div className="trow head table-head" style={{ gridTemplateColumns: COLUMNS }}>
                <span>Session</span>
                <span>Companies</span>
                <span>State</span>
                <span>Contract</span>
                <span>Round</span>
                <span style={{ textAlign: "right" }}>Cost</span>
                <span style={{ textAlign: "right" }}>Code shared</span>
                <span />
              </div>
              {rows.map((session) => {
                const view = loaded(session.id);
                const failure =
                  typeof views[session.id] === "string" ? (views[session.id] as string) : null;
                return (
                  <div
                    key={session.id}
                    className="trow click"
                    style={{ gridTemplateColumns: COLUMNS }}
                    onClick={() => navigate(sessionPath(session.id))}
                  >
                    <div className="ellipsis">
                      <div style={{ fontWeight: 500 }} className="ellipsis">
                        {view?.name ?? session.name}
                      </div>
                      <div className="muted mono">{session.id}</div>
                    </div>
                    <span className="row wrap" style={{ gap: 6 }}>
                      {view?.parties.map((party) => (
                        <span key={party.party} className="row" style={{ gap: 5 }}>
                          <Swatch tone={roleTone(party.role)} size={7} />
                          {party.display_name}
                        </span>
                      )) ?? <span className="muted">—</span>}
                    </span>
                    <span>
                      {view ? (
                        <StateBadge state={view.state} />
                      ) : failure ? (
                        <Badge tone="e">No access</Badge>
                      ) : (
                        <span className="spinner" />
                      )}
                    </span>
                    <span className="mono fg2">
                      {view
                        ? `v${view.signed_contract_version ?? view.current_contract_version}`
                        : "—"}
                    </span>
                    <span className="mono fg2">
                      {view ? `${view.round} / ${view.max_rounds}` : "—"}
                    </span>
                    <span className="mono" style={{ textAlign: "right" }}>
                      {view
                        ? usd(view.metrics.cost_usd + (view.metrics.sandbox_cost_usd ?? 0))
                        : "—"}
                    </span>
                    <span className="mono" style={{ textAlign: "right", color: "var(--o)" }}>
                      {view ? `${view.metrics.code_lines_shared} lines` : "—"}
                    </span>
                    <span style={{ textAlign: "right" }}>
                      <button
                        type="button"
                        className="btn btn-ghost btn-sm"
                        title="Forget this session in this browser"
                        onClick={(event) => {
                          event.stopPropagation();
                          forgetSession(session.id);
                        }}
                      >
                        ✕
                      </button>
                    </span>
                  </div>
                );
              })}
            </div>
          </div>
        </Card>
      )}
      <div className="home-forms">
        <NewSession />
        <OpenLink />
      </div>
    </div>
  );
}
