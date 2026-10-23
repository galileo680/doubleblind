import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Certificate, PublicSession } from "../api/types";
import { usd } from "../lib/format";
import { useAccount } from "../state/account";
import { authPath, navigate, sessionPath } from "../state/route";
import { fromTokens, saveSession } from "../state/sessions";

function Fact({ value, label }: { value: string; label: string }) {
  return (
    <div className="landing-fact">
      <div className="value">{value}</div>
      <div className="muted">{label}</div>
    </div>
  );
}

export function LandingPage() {
  const account = useAccount();
  const [run, setRun] = useState<PublicSession | null | undefined>(undefined);
  const [certificate, setCertificate] = useState<Certificate | null>(null);

  useEffect(() => {
    api.publicSessions().then(
      (sessions) => {
        const recorded = sessions.find((session) => session.state === "CERTIFIED") ?? null;
        setRun(recorded);
        if (recorded)
          api.certificate(recorded.session_id, recorded.token).then(setCertificate, () => null);
      },
      () => setRun(null),
    );
  }, []);

  const watch = () => {
    if (!run) return;
    saveSession(fromTokens(run.session_id, run.name, { observer: run.token }));
    navigate(`${sessionPath(run.session_id)}?replay=1`);
  };

  return (
    <div className="page landing" data-testid="landing">
      <div className="landing-hero">
        <h1>Two companies' AI agents build an integration. Neither sees the other's code.</h1>
        <p className="fg2">
          The companies agree what the integration must do. Each company's agent reads only its own
          repository, the agents negotiate an API contract with contract tests, each builds its own
          side in its own Token Factory sandbox, and a neutral gateway certifies the integration end
          to end. Only contract tests cross the company boundary.
        </p>
        {certificate && (
          <div className="landing-facts">
            <Fact
              value={String(certificate.metrics.code_lines_shared)}
              label="lines of code shared between the companies"
            />
            <Fact
              value={`${certificate.gateway.requests} · ${certificate.gateway.schema_violations}`}
              label="end-to-end requests through the gateway · contract violations"
            />
            <Fact
              value={usd(certificate.costs.total_usd)}
              label="models and sandboxes, whole run"
            />
          </div>
        )}
      </div>
      <div className="landing-cards">
        <section className="card landing-card">
          <h2>Watch a recorded integration</h2>
          <p className="fg2">
            About a minute, no sign-up. Switch “View as” between the two companies to see what each
            side can and cannot see.
          </p>
          <button
            type="button"
            className="btn btn-primary btn-lg"
            onClick={watch}
            disabled={!run}
            data-testid="watch-recorded"
          >
            ▶ Watch the recorded run
          </button>
          {run === null && <span className="muted">No recorded run is published yet.</span>}
        </section>
        <section className="card landing-card">
          <h2>Use it with your company</h2>
          <p className="fg2">
            Create an account, invite your partner company, agree the scope, then connect your agent
            next to your code.
          </p>
          {account.status === "signed-in" ? (
            <a className="btn btn-lg" href="#/integrations">
              Go to your integrations
            </a>
          ) : (
            <div className="row wrap">
              <a className="btn btn-lg" href={authPath("signup", "#/integrations")}>
                Create a company account
              </a>
              <a className="btn btn-lg" href={authPath("login", "#/integrations")}>
                Sign in
              </a>
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
