import { Shell } from "./components/Shell";
import { Empty } from "./components/ui";
import { AuthPage } from "./pages/AuthPage";
import { CertificatePage } from "./pages/CertificatePage";
import { ContractPage } from "./pages/ContractPage";
import { GatewayPage } from "./pages/GatewayPage";
import { HomePage } from "./pages/HomePage";
import { IntegrationPage } from "./pages/IntegrationPage";
import { IntegrationsPage } from "./pages/IntegrationsPage";
import { InvitePage } from "./pages/InvitePage";
import { IsolationPage } from "./pages/IsolationPage";
import { PresentationPage } from "./pages/PresentationPage";
import { SessionPage } from "./pages/SessionPage";
import { useRoute, type Route, type SessionTab } from "./state/route";
import { SessionScope, useSessionContext } from "./state/session";
import { useTheme } from "./state/theme";

const PAGES: Record<SessionTab, () => React.JSX.Element> = {
  dashboard: SessionPage,
  contract: ContractPage,
  e2e: GatewayPage,
  isolation: IsolationPage,
  certificate: CertificatePage,
};

function SessionRoute({ tab }: { tab: SessionTab }) {
  const { stored, token } = useSessionContext();
  if (!stored || !token) {
    return (
      <div className="page">
        <Empty
          title="No access to this session in this browser"
          action={
            <a className="btn" href="#/">
              Back to sessions
            </a>
          }
        >
          Open the session with a link that carries its tokens (the session page has “Copy link”),
          or create a new session.
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
      return <HomePage />;
    case "login":
    case "signup":
      return <AuthPage key={route.page} mode={route.page} next={route.next} />;
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
