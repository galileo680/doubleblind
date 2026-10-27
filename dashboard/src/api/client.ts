import type {
  Account,
  BrokerEvent,
  Certificate,
  CreatedSession,
  Integration,
  InvitePreview,
  PublicSession,
  Question,
  Role,
  SessionView,
} from "./types";

export const API = "/api/v1";

export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

function errorMessage(text: string, fallback: string): string {
  try {
    const body = JSON.parse(text) as { message?: string; detail?: unknown };
    return body.message ?? JSON.stringify(body.detail ?? body);
  } catch {
    return text || fallback;
  }
}

async function request<T>(path: string, token: string | null, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetch(`${API}${path}`, { ...init, headers });
  const text = await response.text();
  if (!response.ok) throw new ApiError(response.status, errorMessage(text, response.statusText));
  const type = response.headers.get("content-type") ?? "";
  return (type.includes("json") ? JSON.parse(text) : text) as T;
}

const session = (id: string) => `/sessions/${encodeURIComponent(id)}`;
const integration = (id: string) => `/integrations/${encodeURIComponent(id)}`;

export function errorText(failure: unknown): string {
  return failure instanceof Error ? failure.message : String(failure);
}

export const api = {
  me: () => request<Account>("/auth/me", null),
  signup: (email: string, password: string, company: string) =>
    request<Account>("/auth/signup", null, {
      method: "POST",
      body: JSON.stringify({ email, password, company }),
    }),
  login: (email: string, password: string) =>
    request<Account>("/auth/login", null, {
      method: "POST",
      body: JSON.stringify({ email, password }),
    }),
  logout: () => request<unknown>("/auth/logout", null, { method: "POST" }),
  integrations: () => request<Integration[]>("/integrations", null),
  integration: (id: string) => request<Integration>(integration(id), null),
  createIntegration: (name: string, role: Role, scope: string) =>
    request<Integration>("/integrations", null, {
      method: "POST",
      body: JSON.stringify({ name, role, scope }),
    }),
  proposeScope: (id: string, text: string) =>
    request<Integration>(`${integration(id)}/scope`, null, {
      method: "POST",
      body: JSON.stringify({ text }),
    }),
  acceptScope: (id: string, version: number) =>
    request<Integration>(`${integration(id)}/scope/accept`, null, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
  declineScope: (id: string) =>
    request<Integration>(`${integration(id)}/scope/decline`, null, { method: "POST" }),
  newInvite: (id: string) =>
    request<Integration & { invite_token: string }>(`${integration(id)}/invite`, null, {
      method: "POST",
    }),
  newAgentKey: (id: string) =>
    request<{ agent_key: string }>(`${integration(id)}/agent-key`, null, { method: "POST" }),
  startLive: (id: string) =>
    request<{ session_id: string }>(`${integration(id)}/start`, null, { method: "POST" }),
  stopLive: (id: string) => request<unknown>(`${integration(id)}/stop`, null, { method: "POST" }),
  revokeAgentKey: (id: string) =>
    request<unknown>(`${integration(id)}/agent-key`, null, { method: "DELETE" }),
  invite: (token: string) => request<InvitePreview>(`/invites/${encodeURIComponent(token)}`, null),
  acceptInvite: (token: string) =>
    request<Integration>(`/invites/${encodeURIComponent(token)}/accept`, null, { method: "POST" }),
  publicSessions: () => request<PublicSession[]>("/public/sessions", null),
  createSession: (name: string, preset: string, adminToken: string | null) =>
    request<CreatedSession>("/sessions", adminToken, {
      method: "POST",
      body: JSON.stringify({ name, preset }),
    }),
  start: (id: string, token: string) =>
    request<SessionView>(`${session(id)}/start`, token, { method: "POST" }),
  session: (id: string, token: string | null) => request<SessionView>(session(id), token),
  events: (id: string, token: string | null, since = 0) =>
    request<BrokerEvent[]>(`${session(id)}/events?since=${since}`, token),
  questions: (id: string, token: string | null) =>
    request<Question[]>(`${session(id)}/questions`, token),
  contract: (id: string, token: string | null, version: number) =>
    request<string>(`${session(id)}/contract?version=${version}`, token),
  diff: (id: string, token: string | null, from: number, to: number) =>
    request<string>(`${session(id)}/contract/diff?from=${from}&to=${to}`, token),
  answer: (id: string, qid: string, token: string | null, answer: string) =>
    request<unknown>(`${session(id)}/questions/${encodeURIComponent(qid)}/answer`, token, {
      method: "POST",
      body: JSON.stringify({ answer }),
    }),
  sign: (id: string, token: string | null, version: number) =>
    request<unknown>(`${session(id)}/signatures`, token, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
  certificate: (id: string, token: string | null) =>
    request<Certificate>(`${session(id)}/certificate`, token),
};

const tokenParam = (token: string | null) => (token ? `&token=${encodeURIComponent(token)}` : "");

export function streamUrl(id: string, token: string | null, since: number): string {
  return `${API}${session(id)}/events/stream?since=${since}${tokenParam(token)}`;
}

export function certificateHtmlUrl(id: string, token: string | null): string {
  return `${API}${session(id)}/certificate?format=html${tokenParam(token)}`;
}
