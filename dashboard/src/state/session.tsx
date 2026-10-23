import {
  createContext,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type { PartyView } from "../api/types";
import { roleTone, type Tone } from "../lib/model";
import { replayQuestions, replaySession, stepDelay, type ReplaySpeed } from "../lib/replay";
import { useAccount } from "./account";
import { importFromParams, useStoredSession, type StoredSession } from "./sessions";
import { useSessionData, type SessionData } from "./useSessionData";

export const OBSERVER = "observer";

export interface ViewOption {
  value: string;
  label: string;
  tone: Tone | "fg3";
  disabled: boolean;
}

export interface ReplayControls {
  active: boolean;
  recorded: boolean;
  playing: boolean;
  position: number;
  total: number;
  speed: ReplaySpeed;
  start: () => void;
  toggle: () => void;
  stop: () => void;
  setSpeed: (speed: ReplaySpeed) => void;
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
  replay: ReplayControls;
  simulated: boolean;
  canAct: boolean;
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
  return view === OBSERVER
    ? (stored.observer ?? null)
    : (stored.humans[view] ?? stored.observer ?? null);
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
  const account = useAccount();
  const [view, setViewState] = useState(() => initialView(id, stored));
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (!importFromParams(id, params)) return;
    const kept = new URLSearchParams();
    for (const key of ["present", "replay"])
      if (params.get(key)) kept.set(key, params.get(key) ?? "");
    const clean = `${location.hash.split("?")[0]}${kept.size ? `?${kept}` : ""}`;
    history.replaceState(null, "", `${location.pathname}${location.search}${clean}`);
  }, [id, params]);

  useEffect(() => {
    if (!tokenFor(stored, view)) setViewState(initialView(id, stored));
  }, [id, stored, view]);

  const token = tokenFor(stored, view);
  const cookie = token === null && account.status === "signed-in";
  const live = useSessionData(id, token, token !== null || cookie);
  const recorded = live.session?.recorded ?? false;
  const shownView = cookie ? (live.session?.you ?? OBSERVER) : view;
  const simulated = !cookie && shownView !== OBSERVER && !stored?.humans[shownView];

  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), recorded ? 250 : 1000);
    return () => window.clearInterval(timer);
  }, [recorded]);

  const [position, setPosition] = useState<number | null>(null);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState<ReplaySpeed>(params.get("replay") === "1" ? "fast" : "normal");
  const total = live.events.length;
  const autoplay = useRef(params.get("replay") === "1");

  useEffect(() => {
    if (autoplay.current && total > 0) {
      autoplay.current = false;
      setPosition(1);
      setPlaying(true);
    }
  }, [total]);

  useEffect(() => {
    if (position === null || !playing) return;
    if (position >= total) {
      setPlaying(false);
      return;
    }
    const timer = window.setTimeout(
      () => setPosition((current) => (current ?? 0) + 1),
      stepDelay(live.events[position], speed),
    );
    return () => window.clearTimeout(timer);
  }, [position, playing, total, speed, live.events]);

  const shown = recorded
    ? live.events.filter((event) => Date.parse(event.ts) <= now).length
    : position;

  const data = useMemo<SessionData>(() => {
    const seen = (visibility: string) =>
      !simulated || visibility === "shared" || visibility === shownView;
    if (shown === null) {
      if (!simulated) return live;
      return {
        ...live,
        events: live.events.filter((event) => seen(event.visibility)),
        questions: live.questions.filter((question) => question.party === shownView),
      };
    }
    const events = live.events.slice(0, shown).filter((event) => seen(event.visibility));
    return {
      ...live,
      events,
      session: live.session ? replaySession(live.session, events) : null,
      questions: replayQuestions(events),
    };
  }, [live, shown, simulated, shownView]);

  const replay: ReplayControls = {
    active: shown !== null,
    recorded,
    playing,
    position: shown ?? total,
    total,
    speed,
    start: () => {
      setPosition(1);
      setPlaying(true);
    },
    toggle: () => {
      if (position !== null && position >= total) setPosition(1);
      setPlaying((current) => !current);
    },
    stop: () => {
      setPosition(null);
      setPlaying(false);
    },
    setSpeed,
  };
  const shownNow =
    position !== null && !recorded ? new Date(data.events.at(-1)?.ts ?? Date.now()).getTime() : now;

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
        disabled: cookie
          ? party.party !== shownView
          : !stored?.humans[party.party] && !stored?.observer,
      })),
      {
        value: OBSERVER,
        label: "Observer",
        tone: "fg3" as const,
        disabled: cookie || !stored?.observer,
      },
    ];
  }, [parties, stored, cookie, shownView]);

  const ownParty =
    shownView === OBSERVER ? null : (parties?.find((party) => party.party === shownView) ?? null);

  return (
    <SessionContext.Provider
      value={{
        id,
        stored,
        view: shownView,
        setView,
        token,
        ownParty,
        viewOptions,
        data,
        now: shownNow,
        replay,
        simulated,
        canAct: !recorded && !simulated,
      }}
    >
      {children}
    </SessionContext.Provider>
  );
}
