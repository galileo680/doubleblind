import { useEffect, useState, type ReactNode } from "react";
import { signOut, useAccount } from "../state/account";
import { authPath, sessionPath, type Route, type SessionTab } from "../state/route";
import { useOptionalSession } from "../state/session";
import { Logo } from "./ui";

const SESSION_NAV: [SessionTab, string, string][] = [
  ["dashboard", "Session dashboard", "SD"],
  ["contract", "Contract", "CO"],
  ["e2e", "End-to-end", "E2"],
  ["certificate", "Certificate", "CE"],
];

const TAB_LABEL = Object.fromEntries(SESSION_NAV.map(([tab, label]) => [tab, label]));

function useNarrow(limit: number): boolean {
  const [narrow, setNarrow] = useState(() => window.innerWidth < limit);
  useEffect(() => {
    const onResize = () => setNarrow(window.innerWidth < limit);
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, [limit]);
  return narrow;
}

function NavItem({
  href,
  label,
  glyph,
  sub,
  count,
  on,
  wide,
}: {
  href: string;
  label: string;
  glyph: string;
  sub?: string;
  count?: number;
  on: boolean;
  wide: boolean;
}) {
  return (
    <a className={`nav-item${on ? " on" : ""}`} href={href} title={label}>
      <span className="nav-glyph">{glyph}</span>
      {wide && (
        <>
          <span className="nav-text">
            <span>{label}</span>
            {sub && <span className="muted ellipsis">{sub}</span>}
          </span>
          {count ? <span className="nav-count">{count}</span> : null}
        </>
      )}
    </a>
  );
}

function AccountMenu() {
  const account = useAccount();
  if (account.status === "loading") return null;
  if (account.status === "signed-out") {
    return (
      <a className="btn btn-sm btn-primary" href={authPath("login", "#/integrations")}>
        Sign in
      </a>
    );
  }
  return (
    <span className="row" style={{ gap: 8 }} data-testid="account-menu">
      <span className="row" style={{ fontWeight: 500, fontSize: 13 }} title={account.account.email}>
        <span className="avatar">{account.account.company.slice(0, 1).toUpperCase()}</span>
        {account.account.company}
      </span>
      <button type="button" className="btn btn-sm" onClick={() => void signOut()}>
        Sign out
      </button>
    </span>
  );
}

export function Shell({
  route,
  theme,
  onToggleTheme,
  children,
}: {
  route: Route;
  theme: string;
  onToggleTheme: () => void;
  children: ReactNode;
}) {
  const wide = !useNarrow(1200);
  const signedIn = useAccount().status === "signed-in";
  const scope = useOptionalSession();
  const session = scope?.data.session;
  const name = session?.name ?? scope?.stored?.name ?? scope?.id;
  const pair = session?.parties.map((party) => party.display_name).join(" ↔ ");
  const openQuestions = session?.open_questions ?? 0;

  return (
    <div className="app">
      <aside className={`sidebar${wide ? "" : " narrow"}`}>
        <a className="brand" href="#/">
          <Logo size={20} />
          {wide && (
            <div style={{ minWidth: 0 }}>
              <div>DoubleBlind</div>
              <div className="brand-sub">Integrations without shared code</div>
            </div>
          )}
        </a>
        <nav className="nav">
          <div className="nav-group">
            {wide && <span className="label">Workspace</span>}
            <NavItem href="#/" label="Home" glyph="HO" on={route.page === "home"} wide={wide} />
            {signedIn && (
              <NavItem
                href="#/integrations"
                label="Integrations"
                glyph="IN"
                on={route.page === "integrations" || route.page === "integration"}
                wide={wide}
              />
            )}
          </div>
          {route.page === "session" && (
            <div className="nav-group">
              {wide && <span className="label">Session</span>}
              {SESSION_NAV.map(([tab, label, glyph]) => (
                <NavItem
                  key={tab}
                  href={sessionPath(route.id, tab)}
                  label={label}
                  glyph={glyph}
                  sub={tab === "dashboard" ? pair : undefined}
                  count={tab === "dashboard" && openQuestions ? openQuestions : undefined}
                  on={route.tab === tab}
                  wide={wide}
                />
              ))}
              <NavItem
                href={sessionPath(route.id, "dashboard", true)}
                label="Presentation mode"
                glyph="PM"
                on={false}
                wide={wide}
              />
            </div>
          )}
        </nav>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="crumbs">
            {route.page === "sessions" ? (
              <a href="#/sessions">Sessions</a>
            ) : route.page === "integrations" ||
              route.page === "integration" ||
              (route.page === "session" && !scope?.stored) ? (
              <a href="#/integrations">Integrations</a>
            ) : (
              <a href="#/">DoubleBlind</a>
            )}
            {route.page === "session" && (
              <>
                <span className="sep">/</span>
                <a href={sessionPath(route.id)} className="ellipsis">
                  {name}
                </a>
                <span className="sep">/</span>
                <span className="here">{TAB_LABEL[route.tab]}</span>
              </>
            )}
          </div>
          {scope && (
            <span
              className={`live${scope.data.live ? " on" : ""}`}
              title="Live event stream from the broker"
            >
              <span className={`dot${scope.data.live ? " pulse" : ""}`} />
              {scope.data.live ? "Live" : "Connecting"}
            </span>
          )}
          <span className="grow" />
          <AccountMenu />
          <button type="button" className="btn btn-sm theme-btn" onClick={onToggleTheme}>
            <span className="half" />
            {theme === "dark" ? "Dark" : "Light"}
          </button>
        </header>
        <div className="grow">{children}</div>
      </main>
    </div>
  );
}
