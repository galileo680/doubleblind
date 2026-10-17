export type SessionState =
  | "CREATED"
  | "BRIEFING"
  | "NEGOTIATING"
  | "ESCALATED"
  | "AWAITING_SIGNATURES"
  | "IMPLEMENTING"
  | "AMENDING"
  | "E2E"
  | "CERTIFIED"
  | "FAILED";

export type Role = "consumer" | "provider";

export interface BrokerEvent {
  seq: number;
  session_id: string;
  ts: string;
  type: string;
  actor: string;
  visibility: string;
  payload: Record<string, unknown>;
  hash: string;
}

export interface Metrics {
  cost_usd: number;
  sandbox_cost_usd: number;
  tokens: number;
  code_lines_shared: number;
  blocked_count: number;
  redacted_count: number;
  bytes_brief: number;
  bytes_turns: number;
  bytes_results: number;
}

export interface PartyView {
  party: string;
  role: Role;
  display_name: string;
  status: string;
  brief_submitted: boolean;
  binding_submitted: boolean;
}

export interface SessionView {
  id: string;
  name: string;
  preset: string;
  state: SessionState;
  round: number;
  max_rounds: number;
  turn_party: string | null;
  current_contract_version: number;
  signed_contract_version: number | null;
  open_questions: number;
  parties: PartyView[];
  metrics: Metrics;
}

export interface Question {
  id: string;
  party: string;
  text: string;
  options: string[];
  default: string | null;
  answer: string | null;
  answered_by: string | null;
  decision_id: string | null;
}

export interface ScenarioRun {
  id: string;
  title?: string;
  status: string;
  duration_ms?: number;
  failures?: { message: string; step?: number | null }[];
}

export interface TestResult {
  scenario_id: string;
  status: string;
  sanitized_reason?: string | null;
}

export interface CertificateRun {
  party: string;
  contract_version: number;
  summary: Record<string, number>;
  results: TestResult[] | { scenarios: ScenarioRun[] };
}

export interface AuditResult {
  ok: boolean;
  events: number;
  last_hash: string;
  broken_at: number | null;
}

export interface Certificate {
  session_id: string;
  certified: boolean;
  state: SessionState;
  signed_contract_version: number | null;
  contract_sha256: string | null;
  contract_yaml: string | null;
  gateway: { requests: number; refused: number; schema_violations: number };
  costs: { models: Record<string, number>; sandboxes: Record<string, number>; total_usd: number };
  results: Record<string, CertificateRun>;
  audit: AuditResult;
  metrics: Metrics;
}

export type ProbeStatus = "passed" | "failed" | "skipped";

export interface ProbeResult {
  name: string;
  expected: string;
  actual: string;
  status: ProbeStatus;
}

export interface ProbeReport {
  ok: boolean;
  results: ProbeResult[];
}

export interface PublicSession {
  session_id: string;
  name: string;
  state: SessionState;
  created_at: string;
  token: string;
  parties: string[];
}

export interface CreatedSession {
  session_id: string;
  tokens: Record<string, string>;
}

export interface Account {
  id: string;
  email: string;
  company: string;
}

export interface Integration {
  id: string;
  name: string;
  created_at: string;
  your_role: Role;
  companies: Record<Role, string | null>;
  invite_expires_at: number | null;
  agent_key_issued: boolean;
}

export interface InvitePreview {
  name: string;
  invited_by: string | null;
  your_role: Role;
  expires_at: number;
}
