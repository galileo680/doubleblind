import { Shell } from "./components/Shell";
import { Empty } from "./components/ui";
import { AuthPage } from "./pages/AuthPage";
import { CertificatePage } from "./pages/CertificatePage";
import { ContractPage } from "./pages/ContractPage";
import { GatewayPage } from "./pages/GatewayPage";
import { IntegrationPage } from "./pages/IntegrationPage";
import { IntegrationsPage } from "./pages/IntegrationsPage";
import { InvitePage } from "./pages/InvitePage";
import { LandingPage } from "./pages/LandingPage";
import { PresentationPage } from "./pages/PresentationPage";
import { SessionPage } from "./pages/SessionPage";
import { SessionsPage } from "./pages/SessionsPage";
import { useAccount } from "./state/account";
import { authPath, useRoute, type Route, type SessionTab } from "./state/route";
import { SessionScope, useSessionContext } from "./state/session";
import { useTheme } from "./state/theme";

const PAGES: Record<SessionTab, () => React.JSX.Element> = {
  dashboard: SessionPage,
  contract: ContractPage,
  e2e: GatewayPage,
  certificate: CertificatePage,
};

function SessionRoute({ tab }: { tab: SessionTab }) {
  const { token } = useSessionContext();
  const account = useAccount();
  if (!token && account.status === "loading") return null;
  if (!token && account.status === "signed-out") {
    return (
      <div className="page">
        <Empty
          title="No access to this session in this browser"
          action={
            <a className="btn btn-primary" href={authPath("login", location.hash)}>
              Sign in
            </a>
          }
        >
          Sign in with your company account, or open the session with a link that carries its
          tokens.
        </Empty>
      </div>
    );
  }
  const Page = PAGES[tab];
  return <Page />;
}

function TopPage({ route }: { route: Exclude<Route, { page: "session" }> }) {
  switch (route.page) {
    case "home":
      return <LandingPage />;
    case "sessions":
      return <SessionsPage />;
    case "integrations":
      return <IntegrationsPage />;
    case "integration":
      return <IntegrationPage key={route.id} id={route.id} />;
    case "invite":
      return <InvitePage key={route.token} token={route.token} />;
  }
}

export function App() {
  const route = useRoute();
  const [theme, toggleTheme] = useTheme();

  if (route.page === "session" && route.present) {
    return (
      <SessionScope id={route.id} params={route.params}>
        <PresentationPage />
      </SessionScope>
    );
  }

  if (route.page === "login" || route.page === "signup") {
    return <AuthPage key={route.page} mode={route.page} next={route.next} />;
  }

  if (route.page === "session") {
    return (
      <SessionScope key={route.id} id={route.id} params={route.params}>
        <Shell route={route} theme={theme} onToggleTheme={toggleTheme}>
          <SessionRoute tab={route.tab} />
        </Shell>
      </SessionScope>
    );
  }

  return (
    <Shell route={route} theme={theme} onToggleTheme={toggleTheme}>
      <TopPage route={route} />
    </Shell>
  );
}
