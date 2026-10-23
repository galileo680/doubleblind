import { useSyncExternalStore } from "react";

export const SESSION_TABS = ["dashboard", "contract", "e2e", "isolation", "certificate"] as const;
export type SessionTab = (typeof SESSION_TABS)[number];

export type Route =
  | { page: "home" }
  | { page: "sessions" }
  | { page: "login"; next: string }
  | { page: "signup"; next: string }
  | { page: "integrations" }
  | { page: "integration"; id: string }
  | { page: "invite"; token: string }
  | { page: "session"; id: string; tab: SessionTab; present: boolean; params: URLSearchParams };

function subscribe(listener: () => void): () => void {
  window.addEventListener("hashchange", listener);
  return () => window.removeEventListener("hashchange", listener);
}

const currentHash = () => location.hash;

export function parseRoute(hash: string): Route {
  const [path, query = ""] = hash.replace(/^#/, "").split("?");
  const params = new URLSearchParams(query);
  const parts = path.split("/").filter(Boolean);
  if (parts[0] === "login" || parts[0] === "signup")
    return { page: parts[0], next: params.get("next") ?? "#/integrations" };
  if (parts[0] === "integrations") return { page: "integrations" };
  if (parts[0] === "sessions") return { page: "sessions" };
  if (parts[0] === "i" && parts[1])
    return { page: "integration", id: decodeURIComponent(parts[1]) };
  if (parts[0] === "invite" && parts[1]) return { page: "invite", token: parts[1] };
  if (parts[0] !== "s" || !parts[1]) return { page: "home" };
  const tab = SESSION_TABS.find((item) => item === parts[2]) ?? "dashboard";
  const present =
    params.get("present") === "1" || new URLSearchParams(location.search).get("present") === "1";
  return { page: "session", id: decodeURIComponent(parts[1]), tab, present, params };
}

export function useRoute(): Route {
  const hash = useSyncExternalStore(subscribe, currentHash, currentHash);
  return parseRoute(hash);
}

export function sessionPath(id: string, tab: SessionTab = "dashboard", present = false): string {
  const base = `#/s/${encodeURIComponent(id)}${tab === "dashboard" ? "" : `/${tab}`}`;
  return present ? `${base}?present=1` : base;
}

export function integrationPath(id: string): string {
  return `#/i/${encodeURIComponent(id)}`;
}

export function authPath(mode: "login" | "signup", next: string): string {
  return `#/${mode}?next=${encodeURIComponent(next)}`;
}

export function navigate(path: string): void {
  if (location.hash !== path) location.hash = path;
  window.scrollTo(0, 0);
}
