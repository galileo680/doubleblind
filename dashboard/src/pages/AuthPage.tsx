import { useState, type FormEvent } from "react";
import { errorText } from "../api/client";
import { signIn, signUp } from "../state/account";
import { authPath, navigate } from "../state/route";

export function AuthPage({ mode, next }: { mode: "login" | "signup"; next: string }) {
  const signup = mode === "signup";
  const [company, setCompany] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (signup) await signUp(email.trim(), password, company.trim());
      else await signIn(email.trim(), password);
      navigate(next);
    } catch (failure) {
      setError(errorText(failure));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <form className="form-card auth-card" onSubmit={submit} data-testid={`${mode}-form`}>
        <div className="page-head">
          <h1>{signup ? "Create a company account" : "Sign in"}</h1>
        </div>
        {signup && (
          <label className="field">
            Company name
            <input
              className="input"
              value={company}
              onChange={(event) => setCompany(event.target.value)}
              autoComplete="organization"
              maxLength={80}
              required
            />
          </label>
        )}
        <label className="field">
          Work e-mail
          <input
            className="input"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="email"
            required
          />
        </label>
        <label className="field">
          Password
          <input
            className="input"
            type="password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            autoComplete={signup ? "new-password" : "current-password"}
            minLength={signup ? 10 : undefined}
            maxLength={200}
            required
          />
          {signup && <span className="muted">At least 10 characters.</span>}
        </label>
        {error && <div className="alert alert-error">{error}</div>}
        <button className="btn btn-primary" type="submit" disabled={busy}>
          {busy && <span className="spinner" />}
          {signup ? "Create account" : "Sign in"}
        </button>
        <span className="muted">
          {signup ? (
            <>
              Already have an account? <a href={authPath("login", next)}>Sign in</a>
            </>
          ) : (
            <>
              New company? <a href={authPath("signup", next)}>Create an account</a>
            </>
          )}
        </span>
      </form>
    </div>
  );
}
