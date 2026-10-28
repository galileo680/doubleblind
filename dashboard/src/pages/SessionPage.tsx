import { BottomPanels } from "../components/session/Bottom";
import { ContractTabs, NegotiationRoom, SignaturesBar } from "../components/session/Center";
import { SessionHeader } from "../components/session/Header";
import { PartyColumn } from "../components/session/PartyPanels";
import { Skeleton } from "../components/ui";
import { useSessionContext } from "../state/session";

export function SessionPage() {
  const { data, ownParty } = useSessionContext();
  const session = data.session;
  const consumer = session?.parties.find((party) => party.role === "consumer");
  const provider = session?.parties.find((party) => party.role === "provider");
  const left = ownParty?.role === "provider" ? provider : consumer;
  const right = ownParty?.role === "provider" ? consumer : provider;

  return (
    <div data-testid="session-page">
      <SessionHeader />
      {!session ? (
        <div className="session-body">
          <div className="grow card">
            <Skeleton />
          </div>
        </div>
      ) : (
        <>
          <div className="session-body">
            {left && (
              <PartyColumn party={left} own={ownParty?.party === left.party} position="first" />
            )}
            <div className="center-col">
              <div className="party-head">Shared between both companies</div>
              <NegotiationRoom />
              <ContractTabs />
              <SignaturesBar />
            </div>
            {right && (
              <PartyColumn party={right} own={ownParty?.party === right.party} position="last" />
            )}
          </div>
          <BottomPanels />
        </>
      )}
    </div>
  );
}
