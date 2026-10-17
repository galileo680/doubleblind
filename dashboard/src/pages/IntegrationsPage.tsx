import { useEffect, useState, type FormEvent } from "react";
import { api, errorText } from "../api/client";
import type { Integration, Role } from "../api/types";
import { Badge, Card, Empty, Segmented, Spinner } from "../components/ui";
import { otherRole, ROLE_TEXT, roleTone } from "../lib/model";
import { useAccount } from "../state/account";
import { authPath, integrationPath, navigate } from "../state/route";

const COLUMNS = "minmax(220px,1fr) minmax(200px,1fr) 250px";

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
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      navigate(integrationPath((await api.createIntegration(name.trim(), role)).id));
    } catch (failure) {
      setError(errorText(failure));
      setBusy(false);
    }
  };

  return (
    <form className="form-card" onSubmit={submit} data-testid="new-integration">
      <div className="form-step">
        <b>New integration</b>
        <span className="muted">You invite your partner company on the next screen.</span>
      </div>
      <label className="field">
        Name
        <input
          className="input"
          value={name}
          onChange={(event) => setName(event.target.value)}
          placeholder="Spokes ↔ Parcelly shipping"
          maxLength={120}
          required
        />
      </label>
      <div className="field">
        Your side
        <Segmented
          value={role}
          onChange={setRole}
          options={[
            { value: "provider", label: ROLE_TEXT.provider },
            { value: "consumer", label: ROLE_TEXT.consumer },
          ]}
        />
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      <div className="row">
        <span className="grow" />
        <button className="btn btn-primary" type="submit" disabled={busy || !name.trim()}>
          {busy && <span className="spinner" />}
          Create integration
        </button>
      </div>
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
    <div className="page">
      <div className="page-head">
        <div className="grow">
          <h1>Integrations</h1>
          <div className="fg2">
            {account.account.company} · each integration connects your system with one partner
            company
          </div>
        </div>
      </div>
      {error && <div className="alert alert-error">{error}</div>}
      {items === null ? (
        !error && <Spinner />
      ) : items.length === 0 ? (
        <Empty title="No integrations yet">Create one below and invite your partner.</Empty>
      ) : (
        <Card testId="integrations">
          {items.map((item) => (
            <div
              key={item.id}
              className="trow click"
              style={{ gridTemplateColumns: COLUMNS }}
              onClick={() => navigate(integrationPath(item.id))}
            >
              <div className="ellipsis">
                <div style={{ fontWeight: 500 }} className="ellipsis">
                  {item.name}
                </div>
                <div className="muted mono">{item.id}</div>
              </div>
              <span className={item.companies[otherRole(item.your_role)] ? "" : "muted"}>
                {item.companies[otherRole(item.your_role)] ?? "Waiting for partner"}
              </span>
              <span>
                <Badge tone={roleTone(item.your_role)}>{ROLE_TEXT[item.your_role]}</Badge>
              </span>
            </div>
          ))}
        </Card>
      )}
      <NewIntegration />
    </div>
  );
}
