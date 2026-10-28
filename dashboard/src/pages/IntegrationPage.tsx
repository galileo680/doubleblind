import { useEffect, useState, type ReactNode } from "react";
import { api, errorText } from "../api/client";
import type { Integration, Role } from "../api/types";
import { CopyButton } from "../components/session/Header";
import { Badge, Spinner, StateBadge } from "../components/ui";
import { dateTime } from "../lib/format";
import { otherRole, ROLE_TEXT, roleTone } from "../lib/model";
import { useAccount } from "../state/account";
import { integrationPath, navigate, sessionPath } from "../state/route";
import { SignInFirst } from "./IntegrationsPage";

const REFRESH_MS = 4000;

type StepState = "done" | "now" | "todo";

function Step({
  n,
  title,
  state,
  badge,
  testId,
  children,
}: {
  n: number;
  title: string;
  state: StepState;
  badge: ReactNode;
  testId: string;
  children: ReactNode;
}) {
  return (
    <div className="step-card" data-testid={testId}>
      <span className={`step-n ${state}`}>{state === "done" ? "✓" : n}</span>
      <div className="step-body">
        <div className="step-head">
          <div className="step-title">{title}</div>
          {badge}
        </div>
        {children}
      </div>
    </div>
  );
}

function Secret({ text, note }: { text: string; note: string }) {
  return (
    <div className="stack" style={{ gap: 6 }}>
      <div className="row wrap">
        <code className="secret">{text}</code>
        <CopyButton text={text} />
      </div>
      <span className="field-hint warn" style={{ fontSize: 12.5 }}>
        {note}
      </span>
    </div>
  );
}

function ScopeStep({
  integration,
  act,
  onChange,
  state,
}: {
  integration: Integration;
  act: (action: () => Promise<void>) => Promise<void>;
  onChange: (next: Integration) => void;
  state: StepState;
}) {
  const [draft, setDraft] = useState<string | null>(null);
  const { scope, companies, your_role: mine } = integration;
  const partner = companies[otherRole(mine)];
  const company = (role: Role) => companies[role] ?? role;
  const answering = scope.status === "proposed" && scope.proposed_by !== mine && partner;
  const badge =
    scope.status === "agreed" ? (
      <Badge tone="o">Agreed</Badge>
    ) : scope.status === "declined" ? (
      <Badge tone="e">Declined</Badge>
    ) : answering ? (
      <Badge tone="w">Your answer needed</Badge>
    ) : (
      <Badge tone="n">Waiting for {partner ?? "your partner"}</Badge>
    );
  const bar =
    scope.status === "agreed" ? "var(--o)" : scope.status === "declined" ? "var(--e)" : "var(--w)";
  const send = (change: () => Promise<Integration>) =>
    act(async () => {
      onChange(await change());
      setDraft(null);
    });

  return (
    <Step n={2} title="Agree on the scope" state={state} badge={badge} testId="scope-step">
      <div className="stack" style={{ gap: 6 }}>
        <blockquote className="scope-text" style={{ borderLeftColor: bar }}>
          {scope.text}
        </blockquote>
        <span className="muted" style={{ fontSize: 12.5 }}>
          Version {scope.version} · proposed by {company(scope.proposed_by)}
          {scope.status === "agreed" && ` · accepted by ${company(otherRole(scope.proposed_by))}`}
          {scope.status === "declined" && ` · declined by ${company(otherRole(scope.proposed_by))}`}
          {answering && " · waiting for your answer"}
        </span>
      </div>
      {draft === null ? (
        <div className="row wrap">
          {answering && (
            <>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => send(() => api.acceptScope(integration.id, scope.version))}
              >
                Accept
              </button>
              <button
                type="button"
                className="btn"
                onClick={() => send(() => api.declineScope(integration.id))}
              >
                Decline
              </button>
            </>
          )}
          <button type="button" className="btn" onClick={() => setDraft(scope.text)}>
            Propose changes
          </button>
        </div>
      ) : (
        <div className="stack" style={{ gap: 8 }}>
          <textarea
            className="input"
            rows={3}
            value={draft}
            onChange={(event) => setDraft(event.target.value)}
            maxLength={2000}
          />
          <div className="row">
            <button
              type="button"
              className="btn btn-primary"
              disabled={draft.trim().length < 10}
              onClick={() => send(() => api.proposeScope(integration.id, draft.trim()))}
            >
              Send proposal
            </button>
            <button type="button" className="btn btn-ghost" onClick={() => setDraft(null)}>
              Cancel
            </button>
          </div>
        </div>
      )}
      <div className="step-history">
        <div className="section-label" style={{ fontSize: 12 }}>
          History
        </div>
        {scope.history.map((entry) => (
          <div key={`${entry.version}-${entry.action}-${entry.at}`} className="row">
            <span className="mono muted" style={{ width: 28, flex: "none" }}>
              v{entry.version}
            </span>
            <span className="grow">
              {entry.action[0].toUpperCase() + entry.action.slice(1)} by {company(entry.role)} ·{" "}
              {dateTime(entry.at)}
            </span>
          </div>
        ))}
      </div>
    </Step>
  );
}

function Dot({ color }: { color: string }) {
  return <span className="dot" style={{ width: 8, height: 8, background: color }} />;
}

export function IntegrationPage({ id }: { id: string }) {
  const account = useAccount();
  const [integration, setIntegration] = useState<Integration | null>(null);
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  const [agentKey, setAgentKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const signedIn = account.status === "signed-in";

  useEffect(() => {
    if (!signedIn) return;
    const refresh = () =>
      api.integration(id).then(setIntegration, (failure) => setError(errorText(failure)));
    refresh();
    const timer = window.setInterval(refresh, REFRESH_MS);
    return () => window.clearInterval(timer);
  }, [id, signedIn]);

  const act = async (action: () => Promise<void>) => {
    setError(null);
    try {
      await action();
    } catch (failure) {
      setError(errorText(failure));
    }
  };

  if (account.status === "loading") return <Spinner />;
  if (account.status === "signed-out") return <SignInFirst next={integrationPath(id)} />;
  if (!integration) {
    return (
      <div className="page">
        {error ? <div className="alert alert-error">{error}</div> : <Spinner />}
      </div>
    );
  }

  const partnerRole = otherRole(integration.your_role);
  const partner = integration.companies[partnerRole];
  const command = agentKey && `doubleblind connect --broker ${location.origin} --key ${agentKey}`;
  const mine = integration.agents[integration.your_role];
  const theirs = integration.agents[partnerRole];
  const live = integration.sessions.find(
    (run) => !run.recorded && run.state !== "CERTIFIED" && run.state !== "FAILED",
  );
  const agreed = integration.scope.status === "agreed";
  const blocker = !partner
    ? "Your partner has to join first."
    : !agreed
      ? "Both companies must accept the scope first."
      : !mine?.online || !theirs?.online
        ? "Both agents must be online."
        : null;
  const progress = (done: boolean, ready: boolean): StepState =>
    done ? "done" : ready ? "now" : "todo";

  return (
    <div className="page integration-page" data-testid="integration">
      <div className="stack" style={{ gap: 6 }}>
        <div className="row wrap" style={{ gap: 10 }}>
          <h1>{integration.name}</h1>
          <span className={`role-badge tone-${roleTone(integration.your_role)}`}>
            {ROLE_TEXT[integration.your_role]}
          </span>
        </div>
        <div className="mono muted" style={{ fontSize: 12.5 }}>
          {integration.id} · {account.account.company} ↔ {partner ?? "waiting for partner"}
        </div>
      </div>
      {error && <div className="alert alert-error">{error}</div>}

      <Step
        n={1}
        title="Invite your partner"
        state={partner ? "done" : "now"}
        badge={partner ? <Badge tone="o">Joined</Badge> : <Badge tone="n">Waiting</Badge>}
        testId="invite-step"
      >
        {partner ? (
          <span className="fg2">
            {partner} joined as the {partnerRole}. The invite link was used and no longer works.
          </span>
        ) : (
          <>
            <span className="fg2">
              Send your partner company a one-time link. They join with their own company account.
            </span>
            {inviteLink ? (
              <Secret
                text={inviteLink}
                note="Works once and expires after 7 days. Shown only once."
              />
            ) : (
              integration.invite_expires_at && (
                <span className="muted" style={{ fontSize: 12.5 }}>
                  A link is active until{" "}
                  {new Date(integration.invite_expires_at * 1000).toLocaleString()}. Lost it? Create
                  a new one.
                </span>
              )
            )}
            <div className="row">
              <button
                type="button"
                className="btn btn-primary"
                onClick={() =>
                  act(async () => {
                    const renewed = await api.newInvite(id);
                    setIntegration(renewed);
                    setInviteLink(
                      `${location.origin}${location.pathname}#/invite/${renewed.invite_token}`,
                    );
                  })
                }
              >
                {integration.invite_expires_at ? "Create a new link" : "Create invite link"}
              </button>
            </div>
          </>
        )}
      </Step>

      <ScopeStep
        integration={integration}
        act={act}
        onChange={setIntegration}
        state={progress(agreed, Boolean(partner))}
      />

      <Step
        n={3}
        title="Connect your agent"
        state={progress(Boolean(mine?.online), agreed)}
        badge={
          mine?.online ? (
            <Badge tone="o">Agent online</Badge>
          ) : integration.agent_key_issued ? (
            <Badge tone="w">Agent offline</Badge>
          ) : (
            <Badge tone="n">No key</Badge>
          )
        }
        testId="agent-step"
      >
        <span className="fg2">
          Your agent runs on your side, next to your code, and builds your half of the integration.
          Your code never leaves your company.
        </span>
        <ol className="steps-list fg2">
          <li>
            Install the agent once:{" "}
            <code className="inline-code">
              pipx install git+https://github.com/galileo680/doubleblind
            </code>
          </li>
          <li>
            Put <code className="inline-code">NEBIUS_API_KEY</code> and{" "}
            <code className="inline-code">NEBIUS_PROJECT_ID</code> of your Token Factory account in{" "}
            <code className="inline-code">.env</code>, and how to install your app in{" "}
            <code className="inline-code">doubleblind.yaml</code>. Your company pays for its own
            models and sandboxes.
          </li>
          <li>Generate a key and run the command it gives you in your repository.</li>
        </ol>
        {command && (
          <Secret text={command} note="Shown once. Copy it now — a new key stops the old one." />
        )}
        {integration.agent_key_issued && (
          <div className="status-box">
            <div className="row">
              <Dot color={mine?.online ? "var(--o)" : "var(--w)"} />
              {mine?.online ? (
                <span>
                  Your agent is online in <span className="mono">{mine.repo}</span> at commit{" "}
                  <span className="mono">{mine.commit}</span>
                </span>
              ) : (
                <span>Your agent is offline — run the connect command in your repository</span>
              )}
            </div>
            <div className="row fg2">
              <Dot color={!partner ? "var(--line2)" : theirs?.online ? "var(--o)" : "var(--w)"} />
              {partner
                ? `${partner}'s agent is ${theirs?.online ? "online" : "offline"}`
                : "Partner has not joined yet"}
            </div>
          </div>
        )}
        <div className="row wrap">
          <button
            type="button"
            className={integration.agent_key_issued ? "btn" : "btn btn-primary"}
            onClick={() =>
              act(async () => {
                setAgentKey((await api.newAgentKey(id)).agent_key);
                setIntegration({ ...integration, agent_key_issued: true });
              })
            }
          >
            {integration.agent_key_issued ? "Generate a new key" : "Generate agent key"}
          </button>
          {integration.agent_key_issued && (
            <button
              type="button"
              className="btn btn-sm btn-danger"
              onClick={() =>
                act(async () => {
                  await api.revokeAgentKey(id);
                  setAgentKey(null);
                  setIntegration({ ...integration, agent_key_issued: false });
                })
              }
            >
              Revoke key
            </button>
          )}
        </div>
      </Step>

      <Step
        n={4}
        title="Run the integration"
        state={live || !blocker ? "now" : "todo"}
        badge={
          live ? (
            <Badge tone="i">Run live</Badge>
          ) : blocker ? (
            <Badge tone="n">Not ready</Badge>
          ) : (
            <Badge tone="o">Ready</Badge>
          )
        }
        testId="run-step"
      >
        <span className="fg2">
          Start runs the real integration: both agents negotiate the contract, ask your people,
          build and test their own side.
        </span>
        <div className="row wrap" style={{ gap: 12 }}>
          {live ? (
            <>
              <button
                type="button"
                className="btn btn-primary btn-lg"
                onClick={() => navigate(sessionPath(live.id))}
              >
                Open session dashboard
              </button>
              <button
                type="button"
                className="btn btn-lg btn-danger"
                onClick={() => act(async () => void (await api.stopLive(id)))}
              >
                Stop run
              </button>
            </>
          ) : (
            <>
              <button
                type="button"
                className="btn btn-primary btn-lg"
                disabled={blocker !== null}
                onClick={() =>
                  act(async () => navigate(sessionPath((await api.startLive(id)).session_id)))
                }
              >
                Start
              </button>
              {blocker && (
                <span className="muted" style={{ fontSize: 13 }}>
                  {blocker}
                </span>
              )}
            </>
          )}
        </div>
        <div className="runs">
          <div className="runs-head">Runs</div>
          {integration.sessions.length === 0 && (
            <div className="muted" style={{ padding: "16px 14px", fontSize: 13 }}>
              No runs yet.
            </div>
          )}
          {integration.sessions.map((run) => (
            <div key={run.id} className="runs-row" onClick={() => navigate(sessionPath(run.id))}>
              <StateBadge state={run.state} />
              <span className="mono" style={{ fontSize: 12.5 }}>
                {run.id}
              </span>
              {run.recorded && <span className="outline-tag">Recorded run</span>}
              <span className="grow" />
              <span className="muted" style={{ fontSize: 12.5 }}>
                {dateTime(run.created_at)}
              </span>
            </div>
          ))}
        </div>
      </Step>
    </div>
  );
}
