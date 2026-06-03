/**
 * Typed fetch client for the Bajaj Compliance backend.
 * Base URL comes from NEXT_PUBLIC_API_BASE (defaults to http://localhost:8000).
 * In the browser, requests proxy through /api/* (see next.config.ts rewrites).
 */
import type {
  ComplianceResults,
  DashboardSummary,
  KnowledgeBaseSearchResponse,
  ProjectionResponse,
  Rule,
  Submission,
  TimeseriesResponse,
  TopRulesResponse,
} from "./types";

// Server-side fetches run inside the container and need the docker DNS name.
// Browser fetches go through Next.js's /api/* proxy (see next.config rewrites).
const SERVER_BASE =
  process.env.INTERNAL_API_BASE ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://localhost:8000";
const isServer = typeof window === "undefined";
const base = () => (isServer ? SERVER_BASE : "/api");

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, {
    headers: { "Content-Type": "application/json", ...(init?.headers || {}) },
    cache: "no-store",
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text || url}`);
  }
  return (await res.json()) as T;
}

/* ---------- submissions ---------- */
export async function listSubmissions(): Promise<{ submissions: Submission[]; total?: number }> {
  return jsonFetch(`${base()}/submissions`);
}
export async function getSubmission(id: string): Promise<Submission> {
  return jsonFetch(`${base()}/submissions/${id}`);
}
export async function createSubmission(body: {
  title: string;
  content_type: string;
  content?: string;
  file?: File;
}): Promise<Submission> {
  // Backend uses Form(...) + File(...), so we must send multipart/form-data.
  const form = new FormData();
  form.append("title", body.title);
  form.append("content_type", body.content_type);
  if (body.content) form.append("content", body.content);
  if (body.file) form.append("file", body.file);
  const res = await fetch(`${base()}/submissions`, {
    method: "POST",
    body: form,
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return (await res.json()) as Submission;
}
export async function deleteSubmission(id: string): Promise<{ message: string }> {
  return jsonFetch(`${base()}/submissions/${id}`, { method: "DELETE" });
}

/* ---------- compliance ---------- */
export async function analyzeSubmission(id: string) {
  return jsonFetch<{ message: string; submission_id: string; status: string }>(
    `${base()}/compliance/analyze/${id}`,
    { method: "POST" }
  );
}
export async function analyzeSubmissionSync(id: string) {
  return jsonFetch(`${base()}/compliance/analyze/${id}/sync`, { method: "POST" });
}
export async function getComplianceResults(id: string): Promise<ComplianceResults> {
  return jsonFetch(`${base()}/compliance/results/${id}`);
}
export async function getCheck(checkId: string) {
  return jsonFetch(`${base()}/compliance/check/${checkId}`);
}
export async function resumeCheck(id: string, feedback?: string) {
  const qs = feedback ? `?feedback=${encodeURIComponent(feedback)}` : "";
  return jsonFetch(`${base()}/compliance/resume/${id}${qs}`, { method: "POST" });
}

/* ---------- rules ---------- */
export async function listRules(params?: {
  category?: string;
  is_active?: boolean;
  skip?: number;
  limit?: number;
}): Promise<{ total: number; rules: Rule[] }> {
  const qs = new URLSearchParams();
  if (params?.category) qs.set("category", params.category);
  if (params?.is_active !== undefined) qs.set("is_active", String(params.is_active));
  if (params?.skip !== undefined) qs.set("skip", String(params.skip));
  if (params?.limit !== undefined) qs.set("limit", String(params.limit));
  const q = qs.toString();
  return jsonFetch(`${base()}/rules${q ? `?${q}` : ""}`);
}
export async function getRule(id: string): Promise<Rule> {
  return jsonFetch(`${base()}/rules/${id}`);
}
export async function createRule(body: {
  category: string;
  rule_text: string;
  severity?: string;
  keywords?: string[];
  points_deduction?: number;
}): Promise<Rule> {
  return jsonFetch(`${base()}/rules`, { method: "POST", body: JSON.stringify(body) });
}
export async function updateRule(
  id: string,
  body: { is_active?: boolean; severity?: string; rule_text?: string }
): Promise<Rule> {
  const qs = new URLSearchParams();
  if (body.is_active !== undefined) qs.set("is_active", String(body.is_active));
  if (body.severity) qs.set("severity", body.severity);
  if (body.rule_text) qs.set("rule_text", body.rule_text);
  return jsonFetch(`${base()}/rules/${id}?${qs.toString()}`, { method: "PATCH" });
}
export async function deleteRule(id: string) {
  return jsonFetch(`${base()}/rules/${id}`, { method: "DELETE" });
}
export async function generateRulesFromDocument(form: FormData) {
  const res = await fetch(`${base()}/rules/generate-from-document`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

/* ---------- dashboard ---------- */
export async function getDashboardSummary(): Promise<DashboardSummary> {
  return jsonFetch(`${base()}/dashboard/summary`);
}
export async function getViolationsByCategory() {
  return jsonFetch(`${base()}/dashboard/violations-by-category`);
}
export async function getViolationsBySeverity() {
  return jsonFetch(`${base()}/dashboard/violations-by-severity`);
}
export async function getDashboardTimeseries(
  bucket: "day" | "week" = "day"
): Promise<TimeseriesResponse> {
  return jsonFetch(`${base()}/dashboard/timeseries?bucket=${bucket}`);
}
export async function getTopRules(limit: number = 10): Promise<TopRulesResponse> {
  return jsonFetch(`${base()}/dashboard/top-rules?limit=${limit}`);
}

/* ---------- health ---------- */
export async function health(): Promise<{ status: string; llm_available: boolean }> {
  return jsonFetch(`${base()}/health`);
}

/* ---------- knowledge base ---------- */
export async function getKnowledgeBaseProjection(
  method: "umap" | "pca" = "umap"
): Promise<ProjectionResponse> {
  return jsonFetch(`${base()}/knowledge-base/projection?method=${method}`);
}
export async function searchKnowledgeBase(
  q: string,
  k: number = 8
): Promise<KnowledgeBaseSearchResponse> {
  const qs = new URLSearchParams({ q, k: String(k) });
  return jsonFetch(`${base()}/knowledge-base/search?${qs.toString()}`);
}
