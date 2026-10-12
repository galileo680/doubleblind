import type {
  BrokerEvent,
  Certificate,
  CreatedSession,
  ProbeReport,
  PublicSession,
  Question,
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

export const api = {
  publicSessions: () => request<PublicSession[]>("/public/sessions", null),
  createSession: (name: string, preset: string, adminToken: string | null) =>
    request<CreatedSession>("/sessions", adminToken, {
      method: "POST",
      body: JSON.stringify({ name, preset }),
    }),
  start: (id: string, token: string) =>
    request<SessionView>(`${session(id)}/start`, token, { method: "POST" }),
  session: (id: string, token: string) => request<SessionView>(session(id), token),
  events: (id: string, token: string, since = 0) =>
    request<BrokerEvent[]>(`${session(id)}/events?since=${since}`, token),
  questions: (id: string, token: string) => request<Question[]>(`${session(id)}/questions`, token),
  contract: (id: string, token: string, version: number) =>
    request<string>(`${session(id)}/contract?version=${version}`, token),
  diff: (id: string, token: string, from: number, to: number) =>
    request<string>(`${session(id)}/contract/diff?from=${from}&to=${to}`, token),
  answer: (qid: string, token: string, answer: string) =>
    request<unknown>(`/questions/${encodeURIComponent(qid)}/answer`, token, {
      method: "POST",
      body: JSON.stringify({ answer }),
    }),
  sign: (id: string, token: string, version: number) =>
    request<unknown>(`${session(id)}/signatures`, token, {
      method: "POST",
      body: JSON.stringify({ version }),
    }),
  certificate: (id: string, token: string) =>
    request<Certificate>(`${session(id)}/certificate`, token),
  isolationProbe: (id: string, token: string) =>
    request<ProbeReport>(`${session(id)}/isolation-probe`, token, { method: "POST" }),
};

export function streamUrl(id: string, token: string, since: number): string {
  return `${API}${session(id)}/events/stream?since=${since}&token=${encodeURIComponent(token)}`;
}

export function certificateHtmlUrl(id: string, token: string): string {
  return `${API}${session(id)}/certificate?format=html&token=${encodeURIComponent(token)}`;
}
