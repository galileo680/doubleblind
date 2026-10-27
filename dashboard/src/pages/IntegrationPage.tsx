import { useEffect, useState } from "react";
import { api, errorText } from "../api/client";
import type { Integration, Role } from "../api/types";
import { CopyButton } from "../components/session/Header";
import { Badge, Card, CardHead, Spinner, StateBadge } from "../components/ui";
import { dateTime } from "../lib/format";
import { otherRole, ROLE_TEXT } from "../lib/model";
import { useAccount } from "../state/account";
import { integrationPath, navigate, sessionPath } from "../state/route";
import { SignInFirst } from "./IntegrationsPage";

const REFRESH_MS = 4000;

function Secret({ text, note }: { text: string; note: string }) {
  return (
    <>
      <div className="cmd">
        <code>{text}</code>
        <CopyButton text={text} />
      </div>
      <span className="muted">{note}</span>
    </>
  );
}

function ScopeStep({
  integration,
  act,
  onChange,
}: {
  integration: Integration;
  act: (action: () => Promise<void>) => Promise<void>;
  onChange: (next: Integration) => void;
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
      <Badge tone="w">Waiting for {partner ?? "your partner"}</Badge>
    );
  const send = (change: () => Promise<Integration>) =>
    act(async () => {
      onChange(await change());
      setDraft(null);
    });

  return (
    <Card testId="scope-step">
      <CardHead title="2 · Agree on the scope">{badge}</CardHead>
      <div className="form-card" style={{ border: 0 }}>
        <span className="fg2">
          What the integration must do. Both companies accept the same version before the agents
          start, and the agents build exactly this.
        </span>
        <blockquote className="scope-text">{scope.text}</blockquote>
        <span className="muted">
          Version {scope.version} · proposed by {company(scope.proposed_by)}
          {scope.status === "agreed" && ` · accepted by ${company(otherRole(scope.proposed_by))}`}
          {scope.status === "declined" && ` · declined by ${company(otherRole(scope.proposed_by))}`}
        </span>
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
          <>
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
              <button type="button" className="btn" onClick={() => setDraft(null)}>
                Cancel
              </button>
            </div>
          </>
        )}
        {scope.history.length > 1 && (
          <div className="stack" style={{ gap: 2 }}>
            {scope.history.map((entry) => (
              <span key={`${entry.version}-${entry.action}-${entry.at}`} className="muted">
                v{entry.version} {entry.action} by {company(entry.role)} · {dateTime(entry.at)}
              </span>
            ))}
          </div>
        )}
      </div>
    </Card>
  );
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
  const running = integration.sessions.some(
    (run) => !run.recorded && run.state !== "CERTIFIED" && run.state !== "FAILED",
  );
  const agreed = integration.scope.status === "agreed";
  const blocker = !partner
    ? "Your partner has to join first."
    : !agreed
      ? "Both companies must accept the scope first."
      : !mine?.online || !theirs?.online
        ? "Both agents must be online."
        : running
          ? "A live run is in progress."
          : null;

  return (
    <div className="page" data-testid="integration">
      <div className="page-head">
        <div className="grow">
          <h1>{integration.name}</h1>
          <div className="fg2">
            {account.account.company} ({ROLE_TEXT[integration.your_role]}) ↔{" "}
            {partner ? `${partner} (${ROLE_TEXT[partnerRole]})` : "partner not joined yet"}
          </div>
        </div>
      </div>
      {error && <div className="alert alert-error">{error}</div>}

      <Card testId="invite-step">
        <CardHead title="1 · Invite your partner">
          {partner ? <Badge tone="o">Joined</Badge> : <Badge tone="w">Waiting</Badge>}
        </CardHead>
        <div className="form-card" style={{ border: 0 }}>
          {partner ? (
            <span>
              Both companies are in. {partner} is the {partnerRole}.
            </span>
          ) : (
            <>
              <span className="fg2">
                Send a one-time link to your partner company. It works once and expires after 7
                days. A new link cancels the previous one.
              </span>
              {inviteLink ? (
                <Secret text={inviteLink} note="Shown only once." />
              ) : (
                integration.invite_expires_at && (
                  <span className="muted">
                    A link is active until{" "}
                    {new Date(integration.invite_expires_at * 1000).toLocaleString()}. Lost it?
                    Create a new one.
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
        </div>
      </Card>

      <ScopeStep integration={integration} act={act} onChange={setIntegration} />

      <Card testId="agent-step">
        <CardHead title="3 · Connect your agent">
          {mine?.online ? (
            <Badge tone="o">Agent online</Badge>
          ) : integration.agent_key_issued ? (
            <Badge tone="w">Agent offline</Badge>
          ) : (
            <Badge tone="n">No key</Badge>
          )}
        </CardHead>
        <div className="form-card" style={{ border: 0 }}>
          <span className="fg2">
            Your agent runs on your side, next to your code, and builds your half of the
            integration. Your code never leaves your company.
          </span>
          <ol className="fg2" style={{ margin: 0, paddingLeft: 20 }}>
            <li>
              Install the agent once:{" "}
              <span className="mono">
                pipx install git+https://github.com/galileo680/doubleblind
              </span>
            </li>
            <li>
              In your repository, put <span className="mono">NEBIUS_API_KEY</span> and{" "}
              <span className="mono">NEBIUS_PROJECT_ID</span> of your Token Factory account in{" "}
              <span className="mono">.env</span>, and how to install your app in{" "}
              <span className="mono">doubleblind.yaml</span>. Your company pays for its own models
              and sandboxes.
            </li>
            <li>Generate a key and run the command it gives you in your repository.</li>
          </ol>
          {mine?.online && (
            <span>
              Your agent is online in <span className="mono">{mine.repo}</span> at commit{" "}
              <span className="mono">{mine.commit}</span>.
            </span>
          )}
          {partner && (
            <span className="muted">
              {partner}'s agent is {theirs?.online ? "online" : "offline"}.
            </span>
          )}
          {command && (
            <Secret
              text={command}
              note="Run it in your repository. The key is shown only once; a new key stops the old one."
            />
          )}
          <div className="row">
            <button
              type="button"
              className="btn btn-primary"
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
                className="btn"
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
        </div>
      </Card>

      <Card testId="run-step">
        <CardHead title="4 · Run the integration" />
        <div className="form-card" style={{ border: 0 }}>
          <span className="fg2">
            Start runs the real integration: both agents negotiate the contract, ask your people,
            build and test their own side. Each company pays for its own models and sandboxes.
          </span>
          <div className="row wrap">
            <button
              type="button"
              className="btn btn-primary"
              disabled={blocker !== null}
              onClick={() =>
                act(async () => navigate(sessionPath((await api.startLive(id)).session_id)))
              }
            >
              Start
            </button>
            {running && (
              <button
                type="button"
                className="btn"
                onClick={() => act(async () => void (await api.stopLive(id)))}
              >
                Stop run
              </button>
            )}
            {blocker && <span className="muted">{blocker}</span>}
          </div>
        </div>
        {integration.sessions.map((run) => (
          <div
            key={run.id}
            className="trow click"
            style={{ gridTemplateColumns: "160px 1fr 200px" }}
            onClick={() => navigate(sessionPath(run.id))}
          >
            {run.recorded ? <Badge tone="w">Recorded run</Badge> : <StateBadge state={run.state} />}
            <span className="mono muted">{run.id}</span>
            <span className="muted">{dateTime(run.created_at)}</span>
          </div>
        ))}
      </Card>
    </div>
  );
}
