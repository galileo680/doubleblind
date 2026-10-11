import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { PartyView } from "../api/types";
import { roleTone, type Tone } from "../lib/model";
import { importFromParams, useStoredSession, type StoredSession } from "./sessions";
import { useSessionData, type SessionData } from "./useSessionData";

export const OBSERVER = "observer";

export interface ViewOption {
  value: string;
  label: string;
  tone: Tone | "fg3";
  disabled: boolean;
}

export interface SessionContextValue {
  id: string;
  stored: StoredSession | undefined;
  view: string;
  setView: (view: string) => void;
  token: string | null;
  ownParty: PartyView | null;
  viewOptions: ViewOption[];
  data: SessionData;
  now: number;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export function useSessionContext(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) throw new Error("useSessionContext needs a SessionScope");
  return value;
}

export function useOptionalSession(): SessionContextValue | null {
  return useContext(SessionContext);
}

function tokenFor(stored: StoredSession | undefined, view: string): string | null {
  if (!stored) return null;
  return view === OBSERVER ? (stored.observer ?? null) : (stored.humans[view] ?? null);
}

function initialView(id: string, stored: StoredSession | undefined): string {
  const remembered = sessionStorage.getItem(`doubleblind.view.${id}`);
  if (remembered && tokenFor(stored, remembered)) return remembered;
  if (stored?.observer) return OBSERVER;
  return Object.keys(stored?.humans ?? {})[0] ?? OBSERVER;
}

export function SessionScope({
  id,
  params,
  children,
}: {
  id: string;
  params: URLSearchParams;
  children: ReactNode;
}) {
  const stored = useStoredSession(id);
  const [view, setViewState] = useState(() => initialView(id, stored));
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!importFromParams(id, params)) return;
    const present = params.get("present") === "1";
    const clean = `${location.hash.split("?")[0]}${present ? "?present=1" : ""}`;
    history.replaceState(null, "", `${location.pathname}${location.search}${clean}`);
  }, [id, params]);

  useEffect(() => {
    if (!tokenFor(stored, view)) setViewState(initialView(id, stored));
  }, [id, stored, view]);

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, []);

  const token = tokenFor(stored, view);
  const data = useSessionData(id, token);

  const setView = (next: string) => {
    sessionStorage.setItem(`doubleblind.view.${id}`, next);
    setViewState(next);
  };

  const parties = data.session?.parties;
  const viewOptions = useMemo<ViewOption[]>(() => {
    const known =
      parties ??
      Object.keys(stored?.humans ?? {}).map((party) => ({
        party,
        display_name: party,
        role: undefined,
      }));
    return [
      ...known.map((party) => ({
        value: party.party,
        label: party.display_name,
        tone: roleTone(party.role),
        disabled: !stored?.humans[party.party],
      })),
      { value: OBSERVER, label: "Observer", tone: "fg3" as const, disabled: !stored?.observer },
    ];
  }, [parties, stored]);

  const ownParty =
    view === OBSERVER ? null : (parties?.find((party) => party.party === view) ?? null);

  return (
    <SessionContext.Provider
      value={{ id, stored, view, setView, token, ownParty, viewOptions, data, now }}
    >
      {children}
    </SessionContext.Provider>
  );
}
