import { parse } from "yaml";

export interface ContractScenario {
  id: string;
  title: string;
  side: "consumer" | "provider" | "e2e";
  covers: string[];
  steps: Record<string, unknown>[];
}

export interface ContractDecision {
  id: string;
  text: string;
  owner: string;
  origin_question?: string;
}

export interface ContractOperation {
  id: string;
  description?: string;
  method: string;
  path: string;
  requestBody: string;
  responses: string[];
  headers: string[];
}

export interface ContractWebhook {
  id: string;
  description?: string;
  method: string;
  body: string;
  signature?: string;
}

export interface SchemaField {
  name: string;
  type: string;
  required: boolean;
  note: string;
}

export interface ContractView {
  id: string;
  version: number;
  status: string;
  secrets: { name: string; description?: string }[];
  glossary: [string, string][];
  operations: ContractOperation[];
  webhooks: ContractWebhook[];
  schemas: { name: string; fields: SchemaField[] }[];
  mappings: { name: string; pairs: [string, string][] }[];
  decisions: ContractDecision[];
  scenarios: ContractScenario[];
}

type Json = Record<string, unknown>;

const isObject = (value: unknown): value is Json =>
  typeof value === "object" && value !== null && !Array.isArray(value);

const asObject = (value: unknown): Json => (isObject(value) ? value : {});

const refName = (value: unknown): string => {
  const ref = asObject(value).$ref;
  if (typeof ref === "string") return ref.replace("#/schemas/", "");
  return isObject(value) ? "inline" : "—";
};

function schemaType(schema: Json): string {
  if (typeof schema.$ref === "string") return schema.$ref.replace("#/schemas/", "");
  if (schema.const !== undefined) return JSON.stringify(schema.const);
  if (Array.isArray(schema.enum)) return schema.enum.join(" | ");
  if (schema.type === "array") return `${schemaType(asObject(schema.items))}[]`;
  if (typeof schema.type === "string") return schema.type;
  if (Array.isArray(schema.type)) return schema.type.join(" | ");
  if (Array.isArray(schema.oneOf) || Array.isArray(schema.anyOf)) return "one of";
  return "object";
}

function schemaNote(schema: Json): string {
  const notes: string[] = [];
  if (typeof schema.description === "string") notes.push(schema.description);
  if (typeof schema.pattern === "string") notes.push(`pattern ${schema.pattern}`);
  if (typeof schema.format === "string") notes.push(schema.format);
  for (const key of ["minimum", "maximum", "minLength", "maxLength"] as const) {
    if (schema[key] !== undefined) notes.push(`${key} ${String(schema[key])}`);
  }
  return notes.join(" · ");
}

function fields(schema: Json): SchemaField[] {
  const required = new Set(Array.isArray(schema.required) ? schema.required.map(String) : []);
  return Object.entries(asObject(schema.properties)).map(([name, value]) => ({
    name,
    type: schemaType(asObject(value)),
    required: required.has(name),
    note: schemaNote(asObject(value)),
  }));
}

export function parseContract(text: string): ContractView | null {
  let data: Json;
  try {
    data = asObject(parse(text));
  } catch {
    return null;
  }
  const operations = Object.entries(asObject(data.operations)).map(([id, raw]) => {
    const operation = asObject(raw);
    const request = asObject(operation.request);
    const responses = Object.entries(asObject(operation.responses)).map(
      ([status, spec]) => `${status} ${refName(asObject(spec).body)}`,
    );
    return {
      id,
      description: typeof operation.description === "string" ? operation.description : undefined,
      method: String(request.method ?? "—"),
      path: String(request.path ?? "—"),
      requestBody: refName(request.body),
      responses,
      headers: Object.keys(asObject(request.headers)),
    };
  });
  const webhooks = Object.entries(asObject(data.webhooks)).map(([id, raw]) => {
    const webhook = asObject(raw);
    const request = asObject(webhook.request);
    const signature = asObject(webhook.signature);
    return {
      id,
      description: typeof webhook.description === "string" ? webhook.description : undefined,
      method: String(request.method ?? "POST"),
      body: refName(request.body),
      signature: signature.algorithm
        ? `${String(signature.algorithm)} in ${String(signature.header ?? "header")}`
        : undefined,
    };
  });
  const scenarios = (Array.isArray(data.scenarios) ? data.scenarios : [])
    .filter(isObject)
    .map((item): ContractScenario => ({
      id: String(item.id ?? "?"),
      title: String(item.title ?? ""),
      side: (["consumer", "provider", "e2e"].includes(String(item.side))
        ? item.side
        : "e2e") as ContractScenario["side"],
      covers: Array.isArray(item.covers) ? item.covers.map(String) : [],
      steps: Array.isArray(item.steps) ? item.steps.filter(isObject) : [],
    }));
  const decisions = (Array.isArray(data.decisions) ? data.decisions : [])
    .filter(isObject)
    .map((item): ContractDecision => ({
      id: String(item.id ?? "?"),
      text: String(item.text ?? ""),
      owner: String(item.owner ?? ""),
      origin_question: item.origin_question ? String(item.origin_question) : undefined,
    }));
  return {
    id: String(data.id ?? ""),
    version: Number(data.version ?? 0),
    status: String(data.status ?? ""),
    secrets: (Array.isArray(data.secrets) ? data.secrets : []).filter(isObject).map((item) => ({
      name: String(item.name),
      description: item.description ? String(item.description) : undefined,
    })),
    glossary: Object.entries(asObject(data.glossary)).map(([term, text]) => [term, String(text)]),
    operations,
    webhooks,
    schemas: Object.entries(asObject(data.schemas)).map(([name, schema]) => ({
      name,
      fields: fields(asObject(schema)),
    })),
    mappings: Object.entries(asObject(data.semantic_mappings)).map(([name, pairs]) => ({
      name,
      pairs: Object.entries(asObject(pairs)).map(([from, to]) => [from, String(to)]),
    })),
    decisions,
    scenarios,
  };
}
