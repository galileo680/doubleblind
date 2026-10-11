import { useSyncExternalStore } from "react";

export interface StoredSession {
  id: string;
  name: string;
  addedAt: string;
  observer?: string;
  humans: Record<string, string>;
  agents: Record<string, string>;
}

const KEY = "doubleblind.sessions";
const listeners = new Set<() => void>();
let cache: StoredSession[] | null = null;

function read(): StoredSession[] {
  if (cache) return cache;
  try {
    const parsed = JSON.parse(localStorage.getItem(KEY) ?? "[]") as StoredSession[];
    cache = Array.isArray(parsed) ? parsed : [];
  } catch {
    cache = [];
  }
  return cache;
}

function write(sessions: StoredSession[]): void {
  cache = sessions;
  try {
    localStorage.setItem(KEY, JSON.stringify(sessions));
  } catch {
    cache = sessions;
  }
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useStoredSessions(): StoredSession[] {
  return useSyncExternalStore(subscribe, read, read);
}

export function useStoredSession(id: string): StoredSession | undefined {
  return useStoredSessions().find((session) => session.id === id);
}

export function saveSession(session: StoredSession): void {
  const others = read().filter((item) => item.id !== session.id);
  write([session, ...others]);
}

export function forgetSession(id: string): void {
  write(read().filter((item) => item.id !== id));
}

export function fromTokens(
  id: string,
  name: string,
  tokens: Record<string, string>,
): StoredSession {
  const humans: Record<string, string> = {};
  const agents: Record<string, string> = {};
  for (const [key, value] of Object.entries(tokens)) {
    if (key.startsWith("human_")) humans[key.slice(6)] = value;
    if (key.startsWith("agent_")) agents[key.slice(6)] = value;
  }
  return { id, name, addedAt: new Date().toISOString(), observer: tokens.observer, humans, agents };
}

export function importFromParams(id: string, params: URLSearchParams): boolean {
  const tokens: Record<string, string> = {};
  for (const [key, value] of params) {
    if (key === "observer") tokens.observer = value;
    else if (key !== "present" && value) tokens[`human_${key}`] = value;
  }
  if (Object.keys(tokens).length === 0) return false;
  const existing = read().find((session) => session.id === id);
  const imported = fromTokens(id, existing?.name ?? id, tokens);
  saveSession({
    ...imported,
    observer: imported.observer ?? existing?.observer,
    humans: { ...existing?.humans, ...imported.humans },
    agents: existing?.agents ?? {},
  });
  return true;
}

export function shareLink(session: StoredSession): string {
  const params = new URLSearchParams();
  if (session.observer) params.set("observer", session.observer);
  for (const [party, token] of Object.entries(session.humans)) params.set(party, token);
  return `${location.origin}${location.pathname}#/s/${session.id}?${params.toString()}`;
}
