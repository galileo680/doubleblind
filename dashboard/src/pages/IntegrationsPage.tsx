import { useEffect, useState, type FormEvent } from "react";
import { api, errorText } from "../api/client";
import type { Integration, Role } from "../api/types";
import { Empty, Spinner, Swatch } from "../components/ui";
import { otherRole, ROLE_TEXT, roleTone } from "../lib/model";
import { useAccount } from "../state/account";
import { authPath, integrationPath, navigate } from "../state/route";

const SIDES: [Role, string, string][] = [
  ["provider", "Provider", "Exposes the API"],
  ["consumer", "Consumer", "Calls the partner's API"],
];

export function SignInFirst({ next }: { next: string }) {
  return (
    <div className="page">
      <Empty
        title="Sign in to see your integrations"
        action={
          <div className="row">
            <a className="btn btn-primary" href={authPath("login", next)}>
              Sign in
            </a>
            <a className="btn" href={authPath("signup", next)}>
              Create a company account
            </a>
          </div>
        }
      >
        Each company signs in with its own account and sees only its own side.
      </Empty>
    </div>
  );
}

function NewIntegration() {
  const [name, setName] = useState("");
  const [role, setRole] = useState<Role>("provider");
  const [scope, setScope] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      navigate(integrationPath((await api.createIntegration(name.trim(), role, scope.trim())).id));
    } catch (failure) {
      setError(errorText(failure));
      setBusy(false);
    }
  };

  return (
    <form className="form-card new-integration" onSubmit={submit} data-testid="new-integration">
      <div className="form-title">New integration</div>
      <label className="field">
        Name
        <input
          className="input"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Paid order → shipment"
          maxLength={120}
          required
        />
      </label>
      <div className="field">
        Your side
        <div className="side-pick">
          {SIDES.map(([value, title, text]) => (
            <button
              key={value}
              type="button"
              className={`tone-${roleTone(value)}${role === value ? " on" : ""}`}
              onClick={() => setRole(value)}
            >
              <b>
                <Swatch tone={roleTone(value)} size={8} />
                {title}
              </b>
              <span>{text}</span>
            </button>
          ))}
        </div>
      </div>
      <label className="field">
        What should the integration do?
        <textarea
          className="input"
          rows={4}
          value={scope}
          onChange={(event) => setScope(event.target.value)}
          placeholder="When an order is paid, Spokes creates a shipment at Parcelly…"
          minLength={10}
          maxLength={2000}
          required
        />
      </label>
      {error && <div className="alert alert-error">{error}</div>}
      <button
        className="btn btn-primary btn-lg"
        type="submit"
        disabled={busy || !name.trim() || scope.trim().length < 10}
        style={{ justifyContent: "center" }}
      >
        {busy && <span className="spinner" />}
        Create integration
      </button>
      <span className="muted" style={{ fontSize: 12.5 }}>
        You invite your partner company on the next screen.
      </span>
    </form>
  );
}

export function IntegrationsPage() {
  const account = useAccount();
  const [items, setItems] = useState<Integration[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const signedIn = account.status === "signed-in";

  useEffect(() => {
    if (signedIn) api.integrations().then(setItems, (failure) => setError(errorText(failure)));
  }, [signedIn]);

  if (account.status === "loading") return <Spinner />;
  if (account.status === "signed-out") return <SignInFirst next="#/integrations" />;

  return (
    <div className="page" style={{ maxWidth: 1280 }}>
      <div className="page-head">
        <div className="grow">
          <h1>Integrations</h1>
          <div className="fg2">Integrations of {account.account.company}</div>
        </div>
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      <div className="row wrap" style={{ gap: 20, alignItems: "flex-start" }}>
        <div style={{ flex: "2 1 520px", minWidth: 0 }}>
          {items === null ? (
            !error && <Spinner />
          ) : items.length === 0 ? (
            <Empty title="No integrations yet">
              Describe what the integration should do in the card on the right. You invite your
              partner company on the next screen.
            </Empty>
          ) : (
            <div className="card" data-testid="integrations">
              {items.map((item) => {
                const partner = item.companies[otherRole(item.your_role)];
                return (
                  <div
                    key={item.id}
                    className="list-item"
                    onClick={() => navigate(integrationPath(item.id))}
                  >
                    <div style={{ flex: "1 1 220px", minWidth: 0 }}>
                      <div className="ellipsis" style={{ fontWeight: 600 }}>
                        {item.name}
                      </div>
                      <div className="mono muted">{item.id}</div>
                    </div>
                    <div className="row" style={{ flex: "1 1 160px", minWidth: 0 }}>
                      {partner ? (
                        <>
                          <span className="initial">{partner.slice(0, 1).toUpperCase()}</span>
                          <span style={{ fontWeight: 500 }}>{partner}</span>
                        </>
                      ) : (
                        <>
                          <span className="initial empty-initial" />
                          <span className="muted" style={{ fontSize: 14 }}>
                            Waiting for partner
                          </span>
                        </>
                      )}
                    </div>
                    <span className={`role-badge tone-${roleTone(item.your_role)}`}>
                      {ROLE_TEXT[item.your_role]}
                    </span>
                    <span className="muted">›</span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
        <NewIntegration />
      </div>
    </div>
  );
}
