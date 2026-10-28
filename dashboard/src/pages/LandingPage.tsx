import { useEffect, useState } from "react";
import { api } from "../api/client";
import type { Certificate, PublicSession } from "../api/types";
import { usd } from "../lib/format";
import { certificateScenarios } from "../lib/model";
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

  const scenarios = certificate ? certificateScenarios(certificate) : [];

  return (
    <div className="page landing" data-testid="landing">
      <div className="landing-hero">
        <span className="landing-pill">
          <span className="dot" />
          NVIDIA Nemotron on Nebius Token Factory
        </span>
        <h1>Two companies' AI agents build an integration. Neither sees the other's code.</h1>
        <p className="fg2">
          The two companies agree on a scope. Each agent reads only its own repository. The agents
          negotiate an API contract with contract tests, then each builds its own side in its own
          sandbox. A neutral gateway certifies the integration end to end. Only the contract and
          test results cross the company boundary.
        </p>
      </div>
      {certificate && run && (
        <div className="stack" style={{ gap: 10 }}>
          <div className="section-label">Latest certified run · {run.parties.join(" ↔ ")}</div>
          <div className="landing-facts">
            <Fact
              value={`${certificate.gateway.requests} · ${certificate.gateway.schema_violations}`}
              label="end-to-end requests through the gateway · contract violations"
            />
            <Fact
              value={usd(certificate.costs.total_usd)}
              label="models and sandboxes for the whole run"
            />
            <Fact
              value={`${scenarios.filter((item) => item.status === "passed").length} / ${scenarios.length}`}
              label="scenarios green on both sides and end to end"
            />
          </div>
        </div>
      )}
      <div className="landing-cards">
        <section className="card landing-card">
          <h2>Watch a recorded integration</h2>
          <p className="fg2">
            About a minute, no sign-up. Switch “View as” between the two companies to see exactly
            what each company sees, and what stays locked.
          </p>
          <span className="grow" />
          <button
            type="button"
            className="btn btn-primary btn-lg"
            onClick={watch}
            disabled={!run}
            data-testid="watch-recorded"
          >
            <span style={{ fontSize: 12 }}>▶</span>
            Watch the recorded run
          </button>
          {run === null && <span className="muted">No recorded run is published yet.</span>}
        </section>
        <section className="card landing-card">
          <h2>Use it with your company</h2>
          <p className="fg2">
            Create an account for your company, describe the integration and invite your partner.
            Your agent runs next to your code, and your company pays for its own models and
            sandboxes.
          </p>
          <span className="grow" />
          {account.status === "signed-in" ? (
            <a className="btn btn-dark btn-lg" href="#/integrations">
              Go to your integrations →
            </a>
          ) : (
            <div className="row wrap" style={{ gap: 10 }}>
              <a className="btn btn-dark btn-lg" href={authPath("signup", "#/integrations")}>
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
