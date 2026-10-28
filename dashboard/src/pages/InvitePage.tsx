import { useEffect, useState } from "react";
import { api, errorText } from "../api/client";
import type { InvitePreview } from "../api/types";
import { otherRole, ROLE_TEXT, roleTone } from "../lib/model";
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

  const expires = invite
    ? new Date(invite.expires_at * 1000).toLocaleDateString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
      })
    : "";

  return (
    <div className="page" style={{ alignItems: "center", paddingTop: 48 }}>
      <div className="form-card invite-card" data-testid="invite">
        {invite && (
          <>
            <div className="row" style={{ gap: 12 }}>
              <span className={`invite-initial tone-${roleTone(otherRole(invite.your_role))}`}>
                {(invite.invited_by ?? "?").slice(0, 1).toUpperCase()}
              </span>
              <span className="muted" style={{ fontSize: 12.5 }}>
                Integration invite · works once · expires {expires}
              </span>
            </div>
            <h1>
              {invite.invited_by} invites your company to build the integration "{invite.name}"
            </h1>
            <div className="row wrap" style={{ gap: 10 }}>
              <span className="fg2" style={{ fontSize: 13 }}>
                Your side:
              </span>
              <span className={`role-badge tone-${roleTone(invite.your_role)}`}>
                {ROLE_TEXT[invite.your_role]}
              </span>
            </div>
            <div className="stack" style={{ gap: 6 }}>
              <div className="section-label">Proposed scope · proposed by {invite.invited_by}</div>
              <blockquote className="scope-text">{invite.scope}</blockquote>
              <span className="muted" style={{ fontSize: 12.5 }}>
                You can accept, decline or propose changes after joining. {invite.invited_by} never
                sees your code — only the contract and test results cross the boundary.
              </span>
            </div>
          </>
        )}
        {!invite && !error && <span className="spinner" />}
        {error && <div className="alert alert-error">{error}</div>}
        {invite && account.status === "signed-in" && (
          <div className="row wrap" style={{ gap: 12, paddingTop: 4 }}>
            <button
              type="button"
              className="btn btn-primary btn-lg"
              onClick={accept}
              disabled={busy}
            >
              {busy && <span className="spinner" />}
              Join as {account.account.company}
            </button>
            <span className="muted" style={{ fontSize: 12.5 }}>
              Signed in as {account.account.company}
            </span>
          </div>
        )}
        {invite && account.status === "signed-out" && (
          <div className="row wrap" style={{ gap: 10, paddingTop: 4 }}>
            <a className="btn btn-primary btn-lg" href={authPath("signup", here)}>
              Create a company account
            </a>
            <a className="btn btn-lg" href={authPath("login", here)}>
              Sign in
            </a>
          </div>
        )}
      </div>
    </div>
  );
}
