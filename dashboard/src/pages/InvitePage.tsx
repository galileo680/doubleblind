import { useEffect, useState } from "react";
import { api, errorText } from "../api/client";
import type { InvitePreview } from "../api/types";
import { ROLE_TEXT } from "../lib/model";
import { useAccount } from "../state/account";
import { authPath, integrationPath, navigate } from "../state/route";

export function InvitePage({ token }: { token: string }) {
  const account = useAccount();
  const [invite, setInvite] = useState<InvitePreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const here = `#/invite/${token}`;

  useEffect(() => {
    api.invite(token).then(setInvite, (failure) => setError(errorText(failure)));
  }, [token]);

  const accept = async () => {
    setBusy(true);
    setError(null);
    try {
      navigate(integrationPath((await api.acceptInvite(token)).id));
    } catch (failure) {
      setError(errorText(failure));
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <div className="form-card auth-card" data-testid="invite">
        <div className="page-head">
          <h1>Integration invite</h1>
        </div>
        {invite && (
          <>
            <span>
              <b>{invite.invited_by}</b> invites your company to build the integration{" "}
              <b>{invite.name}</b>. Your side: <b>{ROLE_TEXT[invite.your_role]}</b>.
            </span>
            <div className="field">
              Proposed scope
              <blockquote className="scope-text">{invite.scope}</blockquote>
              <span className="muted">
                After joining you can accept it, propose changes or decline it.
              </span>
            </div>
            <span className="muted">
              Valid until {new Date(invite.expires_at * 1000).toLocaleString()}. The link works
              once.
            </span>
          </>
        )}
        {error && <div className="alert alert-error">{error}</div>}
        {invite && account.status === "signed-in" && (
          <button type="button" className="btn btn-primary" onClick={accept} disabled={busy}>
            {busy && <span className="spinner" />}
            Join as {account.account.company}
          </button>
        )}
        {invite && account.status === "signed-out" && (
          <div className="row">
            <a className="btn btn-primary" href={authPath("signup", here)}>
              Create a company account
            </a>
            <a className="btn" href={authPath("login", here)}>
              Sign in
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
