import { useSyncExternalStore } from "react";
import { api } from "../api/client";
import type { Account } from "../api/types";

export type AccountState =
  { status: "loading" } | { status: "signed-out" } | { status: "signed-in"; account: Account };

const listeners = new Set<() => void>();
let state: AccountState = { status: "loading" };
let loaded = false;

function set(next: AccountState): void {
  state = next;
  listeners.forEach((listener) => listener());
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  if (!loaded) {
    loaded = true;
    api.me().then(
      (account) => set({ status: "signed-in", account }),
      () => set({ status: "signed-out" }),
    );
  }
  return () => listeners.delete(listener);
}

export function useAccount(): AccountState {
  return useSyncExternalStore(
    subscribe,
    () => state,
    () => state,
  );
}

export async function signUp(email: string, password: string, company: string): Promise<void> {
  set({ status: "signed-in", account: await api.signup(email, password, company) });
}

export async function signIn(email: string, password: string): Promise<void> {
  set({ status: "signed-in", account: await api.login(email, password) });
}

export async function signOut(): Promise<void> {
  await api.logout();
  set({ status: "signed-out" });
}
