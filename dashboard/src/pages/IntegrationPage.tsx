import { useEffect, useState } from "react";
import { api, errorText } from "../api/client";
import type { Integration } from "../api/types";
import { CopyButton } from "../components/session/Header";
import { Badge, Card, CardHead, Segmented, Spinner, StateBadge } from "../components/ui";
import { dateTime } from "../lib/format";
import { otherRole, ROLE_TEXT } from "../lib/model";
import { useAccount } from "../state/account";
import { integrationPath, navigate, sessionPath } from "../state/route";
import { SignInFirst } from "./IntegrationsPage";

const REFRESH_MS = 4000;
const DEMO_SPEEDS = ["1", "5", "20"] as const;

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

export function IntegrationPage({ id }: { id: string }) {
  const account = useAccount();
  const [integration, setIntegration] = useState<Integration | null>(null);
  const [inviteLink, setInviteLink] = useState<string | null>(null);
  const [agentKey, setAgentKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [speed, setSpeed] = useState<(typeof DEMO_SPEEDS)[number]>("5");
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

      <Card testId="agent-step">
        <CardHead title="2 · Connect your agent">
          {integration.agent_key_issued ? (
            <Badge tone="o">Key active</Badge>
          ) : (
            <Badge tone="n">No key</Badge>
          )}
        </CardHead>
        <div className="form-card" style={{ border: 0 }}>
          <span className="fg2">
            Your agent runs on your side, next to your code, and builds your half of the
            integration. Your code never leaves your company.
          </span>
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
        <CardHead title="3 · Run the integration" />
        <div className="form-card" style={{ border: 0 }}>
          <span className="fg2">
            Start (DEMO) plays a recorded, certified run between Spokes and Parcelly for both
            companies at once. No agents, no cost.
          </span>
          <div className="row wrap">
            <Segmented
              value={speed}
              onChange={setSpeed}
              options={DEMO_SPEEDS.map((value) => ({ value, label: `${value}×` }))}
            />
            <button
              type="button"
              className="btn btn-primary"
              disabled={!partner}
              title={partner ? undefined : "Your partner has to join first"}
              onClick={() =>
                act(async () =>
                  navigate(sessionPath((await api.startDemo(id, Number(speed))).session_id)),
                )
              }
            >
              Start (DEMO)
            </button>
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
