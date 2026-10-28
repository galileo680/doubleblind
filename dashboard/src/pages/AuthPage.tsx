import { useState, type FormEvent } from "react";
import { errorText } from "../api/client";
import { signIn, signUp } from "../state/account";
import { Logo } from "../components/ui";
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

  const short = signup && password.length > 0 && password.length < 10;

  return (
    <div className="auth-screen">
      <a className="auth-brand" href="#/">
        <Logo size={24} />
        <span>
          <b>DoubleBlind</b>
          <span>Integrations without shared code</span>
        </span>
      </a>
      <form className="form-card auth-card" onSubmit={submit} data-testid={`${mode}-form`}>
        <div>
          <h1>{signup ? "Create a company account" : "Sign in"}</h1>
          <div className="fg2" style={{ marginTop: 4, fontSize: 13.5 }}>
            {signup
              ? "You can invite your partner company after you create the first integration."
              : "Sign in to your company account."}
          </div>
        </div>
        {signup && (
          <label className="field">
            Company name
            <input
              className="input"
              value={company}
              onChange={(event) => setCompany(event.target.value)}
              autoComplete="organization"
              placeholder="Spokes"
              maxLength={80}
              required
            />
          </label>
        )}
        <label className="field">
          {signup ? "Work e-mail" : "E-mail"}
          <input
            className="input"
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            autoComplete="email"
            placeholder="you@company.com"
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
            style={short ? { borderColor: "var(--w)" } : undefined}
            required
          />
          {signup && (
            <span className={`field-hint${short ? " warn" : ""}`}>
              {short
                ? `At least 10 characters · ${10 - password.length} more`
                : "At least 10 characters"}
            </span>
          )}
        </label>
        {error && <div className="alert alert-error">{error}</div>}
        <button className="btn btn-primary btn-lg" type="submit" disabled={busy}>
          {busy && <span className="spinner" />}
          {signup ? "Create account" : "Sign in"}
        </button>
        <div className="row fg2" style={{ justifyContent: "center", fontSize: 13 }}>
          {signup ? "Already have an account?" : "New to DoubleBlind?"}
          <a href={authPath(signup ? "login" : "signup", next)} style={{ fontWeight: 500 }}>
            {signup ? "Sign in" : "Create a company account"}
          </a>
        </div>
      </form>
      <div className="muted">One account is one company. Your partner company creates its own.</div>
    </div>
  );
}
