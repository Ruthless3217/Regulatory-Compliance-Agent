/**
 * Typed fetch client for the Bajaj Compliance backend.
 * Base URL comes from NEXT_PUBLIC_API_BASE (defaults to http://localhost:8000).
 * In the browser, requests proxy through /api/* (see next.config.ts rewrites).
 */
import type {
  Annotation,
  AuditRow,
  CheckSummary,
  ComplianceResults,
  DashboardSummary,
  DiffBlock,
  DocUsageRow,
  DocumentComment,
  DocumentComparison,
  KnowledgeBaseSearchResponse,
  SearchHit,
  LearningCalibration,
  LearningFunnel,
  LearningLatency,
  LearningPrecision,
  Me,
  ModelsHealth,
  ProjectionResponse,
  RagHealth,
  RepeatedPatterns,
  RunDiff,
  RunSummary,
  Rule,
  RuleAuditRow,
  RuleReliabilityHistory,
  RunRow,
  SessionRow,
  Submission,
  SubmissionRevision,
  RevisionSource,
  ReviewerActionType,
  TimeseriesResponse,
  TopRulesResponse,
  UsageSummary,
  UserRow,
  Violation,
  AssignmentOutcome,
  AssignmentPriority,
  AssignmentStatus,
  ReviewAssignment,
  ReviewerTrail,
  TrailRow,
  WorkloadRow,
  CorpusLayer,
  CorpusLayerList,
  CorpusLayerItems,
  CorpusLayerDocument,
  CorpusLayerDeleteResult,
  RetrievalCandidatesInspection,
  RetrievalChunksInspection,
  RetrievalInspection,
  RetrievalRejectedInspection,
} from "./types";

// Server-side fetches run inside the container and need the docker DNS name.
const SERVER_BASE =
  process.env.INTERNAL_API_BASE ||
  process.env.NEXT_PUBLIC_API_BASE ||
  "http://localhost:8000";

// Browser fetch base:
//  - Standalone/local: NEXT_PUBLIC_API_BASE is absolute (http://localhost:8000),
//    so we route through Next.js's same-origin "/api/*" proxy (next.config
//    rewrites). Preserves the original dev behaviour.
//  - Shared platform: NEXT_PUBLIC_API_BASE is a relative path (e.g.
//    "/compliance/api") that nginx maps straight to the backend, so we call it
//    directly. (Raw fetch() is NOT auto-prefixed by basePath, hence this.)
const PUBLIC_API_BASE = process.env.NEXT_PUBLIC_API_BASE || "";
const BROWSER_BASE = PUBLIC_API_BASE.startsWith("/") ? PUBLIC_API_BASE : "/api";

const isServer = typeof window === "undefined";
const base = () => (isServer ? SERVER_BASE : BROWSER_BASE);

// On the server the browser's cookies are NOT attached to outgoing fetches, so
// forward the caller's session cookie to the backend (mirrors the hand-rolled
// pattern in the workspace/super-admin layouts). Guarded + dynamically imported
// so next/headers never reaches the client bundle.
async function serverAuthHeaders(): Promise<Record<string, string>> {
  if (typeof window !== "undefined") return {};
  try {
    const { cookies } = await import("next/headers");
    const sid = (await cookies()).get("rca_session")?.value;
    return sid ? { Cookie: `rca_session=${sid}` } : {};
  } catch {
    return {};
  }
}

async function jsonFetch<T>(url: string, init?: RequestInit): Promise<T> {
  const authHeaders = await serverAuthHeaders();
  const res = await fetch(url, {
    cache: "no-store",
    credentials: "include",
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders, ...(init?.headers || {}) },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text || url}`);
  }
  return (await res.json()) as T;
}

/* ---------- submissions ---------- */
/** Newest first (the route orders by submitted_at). `limit` is capped at 100
 * server-side; the default of 20 is a first page, not the whole set, so any
 * caller that needs to *find* a specific submission must ask for more. */
export async function listSubmissions(
  limit?: number
): Promise<{ submissions: Submission[]; total?: number }> {
  const qs = limit ? `?limit=${limit}` : "";
  return jsonFetch(`${base()}/submissions${qs}`);
}
/** Seed HTML for the editor. Its own request because converting a long
 * DOCX/PDF takes seconds — it used to be computed inside GET /submissions/{id},
 * which made simply opening a document time out. */
export async function getSubmissionImportHtml(
  id: string
): Promise<{
  html: string | null;
  /** imported | unavailable (nothing to import) | failed (conversion error).
   * `reason` is reviewer-facing prose, so it can be rendered directly. */
  status: "imported" | "unavailable" | "failed";
  reason: string | null;
}> {
  return jsonFetch(`${base()}/submissions/${id}/import-html`);
}
export async function getSubmission(id: string): Promise<Submission> {
  return jsonFetch(`${base()}/submissions/${id}`);
}
export async function createSubmission(body: {
  title: string;
  content_type: string;
  product_line: string;
  content?: string;
  file?: File;
}): Promise<Submission> {
  // Backend uses Form(...) + File(...), so we must send multipart/form-data.
  const form = new FormData();
  form.append("title", body.title);
  form.append("content_type", body.content_type);
  form.append("product_line", body.product_line);
  if (body.content) form.append("content", body.content);
  if (body.file) form.append("file", body.file);
  const res = await fetch(`${base()}/submissions`, {
    method: "POST",
    body: form,
    cache: "no-store",
    credentials: "include",
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

/** The one mutation primitive behind manual edits/apply-fix/bulk-apply/restore
 * — records a new content revision and makes it the submission's current
 * content. */
export async function applySubmissionRevision(
  submissionId: string,
  body: {
    content: string;
    source: RevisionSource;
    note?: string;
    applied_violation_ids?: string[];
    // Two views of the working document, written together. `content` stays the
    // plain-text projection findings and search still read.
    // Serialized Lexical editor state. Typed loosely here so lib/api stays
    // free of an editor dependency; the workspace context holds the real type.
    lexical_state?: unknown;
    lexical_html?: string;
  }
): Promise<SubmissionRevision> {
  return jsonFetch(`${base()}/submissions/${submissionId}/revisions`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Full revision history for a submission, oldest first (matches the
 * backend's ordering) — feeds VersionHistoryPopover's View/Restore list. */
export async function listSubmissionRevisions(
  submissionId: string
): Promise<{ revisions: SubmissionRevision[] }> {
  return jsonFetch(`${base()}/submissions/${submissionId}/revisions`);
}

// These URLs are consumed by the browser (<img src>), so they must use the
// browser base on BOTH server render and client — mirrors comparisonPageImageUrl.
export function submissionPageImageUrl(submissionId: string, n: number): string {
  return `${BROWSER_BASE}/submissions/${submissionId}/pages/${n}`;
}

/** Create a freestanding reviewer note anchored to a text selection (or a
 * PDF page number, for PdfPagePane) — mirrors comparisons.py's annotation
 * shape but each comment is its own row. */
export async function createSubmissionComment(
  submissionId: string,
  body: { anchor_text?: string | null; page_number?: number | null; body: string }
): Promise<DocumentComment> {
  return jsonFetch(`${base()}/submissions/${submissionId}/comments`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export type SubmissionExportKind =
  | "clean.docx"
  | "clean.pdf"
  | "annotated.docx"
  | "annotated.pdf"
  | "report.docx"
  | "report.pdf"
  | "feedback-report.docx"
  | "feedback-report.pdf"
  | "bundle.zip";

/** The reviewer's corrections as a redline: uploaded original vs working copy.
 * Same aligner Compare uses between two files. */
export async function getSubmissionDraftDiff(id: string) {
  return jsonFetch<{ blocks: DiffBlock[]; changed: number; edited: boolean }>(
    `${base()}/submissions/${id}/draft-diff`
  );
}

/** Same-origin URL for a submission export artifact (feed to a download
 * anchor / window.open) — mirrors exportComparisonUrl for Compare. */
export function exportSubmissionUrl(id: string, kind: SubmissionExportKind): string {
  return `${BROWSER_BASE}/submissions/${id}/export/${kind}`;
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
export async function getCheck(checkId: string): Promise<CheckSummary> {
  return jsonFetch(`${base()}/compliance/check/${checkId}`);
}

/** Reviewer-facing run history for a submission, oldest to newest — feeds the
 * "Run #N of M" picker and the historical-run banner. */
export async function listSubmissionRuns(
  submissionId: string
): Promise<{ submission_id: string; runs: RunSummary[] }> {
  return jsonFetch(`${base()}/compliance/submissions/${submissionId}/runs`);
}

/** Diff one run's violations against `previous` (the prior run_number) or an
 * explicit run id (e.g. the latest run, for "Compare to latest"). */
export async function diffRun(runId: string, against: string = "previous"): Promise<RunDiff> {
  return jsonFetch(`${base()}/compliance/runs/${runId}/diff?against=${encodeURIComponent(against)}`);
}

/* ---------- adaptive rule weights (HITL feedback) ---------- */
export async function submitViolationFeedback(
  violationId: string,
  body: {
    verdict: "accept" | "reject";
    severity_override?: string;
    comment?: string;
  }
): Promise<{
  violation_id: string;
  rule_id: string | null;
  verdict: string;
  weight_updated: boolean;
  reliability: number | null;
}> {
  return jsonFetch(`${base()}/compliance/violations/${violationId}/feedback`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** The real reviewer-action taxonomy (Correct / Not-a-violation / Dismiss)
 * replacing the binary accept/reject shim above. correct/not_violation still
 * update the fired rule's reliability (delegated server-side); dismiss does
 * not. */
export async function submitReviewerAction(
  violationId: string,
  body: {
    action: ReviewerActionType;
    reason?: string;
    explanation?: string;
    final_text?: string;
    severity_override?: string;
  }
): Promise<{
  violation_id: string;
  rule_id: string | null;
  action: string;
  weight_updated: boolean;
  reliability: number | null;
  routed_queue: string | null;
  review_status: string | null;
  resolved_at: string | null;
}> {
  return jsonFetch(`${base()}/compliance/violations/${violationId}/actions`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}
/* ---------- reviewer-authored findings (migration 0031) ---------- */

/** Flag a span the model never surfaced. Attaches server-side to the
 * submission's LATEST compliance check — 400s (with a readable message) if the
 * submission has never been analysed, since there'd be no check to attach to. */
export async function createReviewerViolation(
  submissionId: string,
  body: {
    current_text: string;
    description: string;
    severity: string;
    category: string;
    suggested_fix?: string;
  }
): Promise<Violation> {
  return jsonFetch(`${base()}/compliance/submissions/${submissionId}/violations`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Delete a reviewer-authored flag (its author, or an admin). A MODEL-authored
 * finding is refused server-side — record a Not-a-violation/Dismiss verdict
 * instead, so the model's own output survives as the evidence its precision is
 * measured against. */
export async function deleteReviewerViolation(
  violationId: string
): Promise<{ message: string; id: string }> {
  return jsonFetch(`${base()}/compliance/violations/${violationId}`, { method: "DELETE" });
}

/** Ask for replacement wording for one finding. Proposes only — nothing is
 * persisted until the reviewer accepts and the text goes through the normal
 * revision path, because every saved edit forces a re-analysis. */
export async function rewriteViolationText(
  violationId: string,
  instruction?: string
): Promise<{
  violation_id: string;
  original_text: string;
  proposed_text: string;
  instruction: string | null;
}> {
  return jsonFetch(`${base()}/compliance/violations/${violationId}/rewrite`, {
    method: "POST",
    body: JSON.stringify({ instruction: instruction?.trim() || null }),
  });
}

// Held-out evaluation only: logs the reviewer's own document score next to
// the system's. Never affects scoring or rule weights.
export async function submitReviewerScore(
  checkId: string,
  score: number
): Promise<{
  check_id: string;
  reviewer_score: number;
  system_score: number | null;
  gap: number | null;
}> {
  return jsonFetch(`${base()}/compliance/check/${checkId}/reviewer-score`, {
    method: "POST",
    body: JSON.stringify({ score }),
  });
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
  product_line: string;
}): Promise<Rule> {
  return jsonFetch(`${base()}/rules`, { method: "POST", body: JSON.stringify(body) });
}
export async function updateRule(
  id: string,
  body: { is_active?: boolean; severity?: string; rule_text?: string; product_line?: string }
): Promise<Rule> {
  const qs = new URLSearchParams();
  if (body.is_active !== undefined) qs.set("is_active", String(body.is_active));
  if (body.severity) qs.set("severity", body.severity);
  if (body.rule_text) qs.set("rule_text", body.rule_text);
  if (body.product_line) qs.set("product_line", body.product_line);
  return jsonFetch(`${base()}/rules/${id}?${qs.toString()}`, { method: "PATCH" });
}
export async function deleteRule(id: string) {
  return jsonFetch(`${base()}/rules/${id}`, { method: "DELETE" });
}
export async function generateRulesFromDocument(form: FormData) {
  const res = await fetch(`${base()}/rules/generate-from-document`, {
    method: "POST",
    body: form,
    credentials: "include",
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

/* ---------- model learning ---------- */
export async function getLearningFunnel(): Promise<LearningFunnel> {
  return jsonFetch(`${base()}/model-learning/funnel`);
}
export async function getLearningPrecision(
  by: "rule" | "category" | "severity" = "rule"
): Promise<LearningPrecision> {
  return jsonFetch(`${base()}/model-learning/precision?by=${by}`);
}
export async function getLearningCalibration(): Promise<LearningCalibration> {
  return jsonFetch(`${base()}/model-learning/calibration`);
}
export async function getRuleReliabilityHistory(ruleId: string): Promise<RuleReliabilityHistory> {
  return jsonFetch(`${base()}/model-learning/rule-reliability-history?rule_id=${ruleId}`);
}
export async function getLearningLatency(): Promise<LearningLatency> {
  return jsonFetch(`${base()}/model-learning/latency`);
}
export async function getRepeatedPatterns(minSubmissions: number = 2): Promise<RepeatedPatterns> {
  return jsonFetch(`${base()}/model-learning/repeated-patterns?min_submissions=${minSubmissions}`);
}

/* ---------- health ---------- */
export async function health(): Promise<{ status: string; llm_available: boolean }> {
  return jsonFetch(`${base()}/health`);
}
export async function healthRag(): Promise<RagHealth> {
  return jsonFetch(`${base()}/health/rag`);
}
export async function healthModels(): Promise<ModelsHealth> {
  return jsonFetch(`${base()}/health/models`);
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

/* ---------- comparisons ---------- */
export async function listComparisons(): Promise<{ total: number; comparisons: DocumentComparison[] }> {
  return jsonFetch(`${base()}/comparisons`);
}
export async function getComparison(id: string): Promise<DocumentComparison> {
  return jsonFetch(`${base()}/comparisons/${id}`);
}
export async function createComparison(body: {
  title: string;
  old_file?: File;
  new_file?: File;
  old_content?: string;
  new_content?: string;
}): Promise<DocumentComparison> {
  const form = new FormData();
  form.append("title", body.title);
  if (body.old_file) form.append("old_file", body.old_file);
  if (body.new_file) form.append("new_file", body.new_file);
  if (body.old_content) form.append("old_content", body.old_content);
  if (body.new_content) form.append("new_content", body.new_content);
  const res = await fetch(`${base()}/comparisons`, {
    method: "POST",
    body: form,
    cache: "no-store",
    credentials: "include",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return (await res.json()) as DocumentComparison;
}
export async function deleteComparison(id: string): Promise<{ message: string }> {
  return jsonFetch(`${base()}/comparisons/${id}`, { method: "DELETE" });
}
// These URLs are consumed by the browser (<img src>, <a href download>), so they
// must use the browser base on BOTH server render and client — otherwise the SSR'd
// markup (SERVER_BASE) mismatches the client (BROWSER_BASE) and React hydration fails.
export function comparisonPageImageUrl(id: string, side: "old" | "new", n: number): string {
  return `${BROWSER_BASE}/comparisons/${id}/pages/${side}/${n}`;
}

/** On-demand per-side word scan. 409 when that side is not a PDF. */
export async function searchComparison(
  id: string,
  side: "old" | "new",
  q: string
): Promise<{ hits: SearchHit[] }> {
  const qs = new URLSearchParams({ side, q });
  return jsonFetch(`${base()}/comparisons/${id}/search?${qs.toString()}`);
}

/** Upsert a reviewer annotation on one change. */
export async function upsertAnnotation(
  id: string,
  body: { change_id: string; note?: string | null; tags?: string[] }
): Promise<Annotation> {
  return jsonFetch(`${base()}/comparisons/${id}/annotations`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Remove the annotation for one change. */
export async function deleteAnnotation(id: string, changeId: string): Promise<{ message: string }> {
  return jsonFetch(`${base()}/comparisons/${id}/annotations/${encodeURIComponent(changeId)}`, {
    method: "DELETE",
  });
}

export type ExportKind =
  | "changes-report.docx"
  | "old-highlighted.pdf"
  | "new-highlighted.pdf"
  | "side-by-side.pdf"
  | "bundle.zip";

/** Same-origin URL for an export artifact (feed to a download anchor / window.open). */
export function exportComparisonUrl(id: string, kind: ExportKind): string {
  return `${BROWSER_BASE}/comparisons/${id}/export/${kind}`;
}

/**
 * Adjust Comparison: re-run in place (same id). Multipart mirrors createComparison
 * (optional old_file/new_file/old_content/new_content + swap). Deletes all
 * annotations server-side. 409 while a render is still processing.
 */
export async function rerunComparison(id: string, form: FormData): Promise<DocumentComparison> {
  const res = await fetch(`${base()}/comparisons/${id}/rerun`, {
    method: "POST",
    body: form,
    cache: "no-store",
    credentials: "include",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return (await res.json()) as DocumentComparison;
}

/* ---------- auth ---------- */
export const login = (b: {username:string; password:string}) => jsonFetch<{ must_change_password?: boolean }>(`${base()}/auth/login`, {method:"POST", body:JSON.stringify(b)});
export const logout = () => jsonFetch(`${base()}/auth/logout`, {method:"POST"});
export const getMe = () => jsonFetch<Me>(`${base()}/auth/me`);
export const heartbeat = () => jsonFetch(`${base()}/auth/heartbeat`, {method:"POST"});
export const changePassword = (b: { current_password: string; new_password: string }) =>
  jsonFetch(`${base()}/auth/change-password`, { method: "POST", body: JSON.stringify(b) });

interface AdminUserApiRow {
  username: string;
  role: string;
  registered_ip?: string | null;
  is_active: boolean;
  last_login_at?: string | null;
  runs?: number | null;
  total_cost_usd?: number | null;
}

interface UsageUserApiRow {
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_cost_usd?: number | null;
  runs?: number | null;
}

interface DocumentUsageApiRow {
  submission_id: string;
  title?: string | null;
  graded_by?: string | null;
  input_tokens?: number | null;
  output_tokens?: number | null;
  total_cost_usd?: number | null;
  total_runs?: number | null;
  last_run?: string | null;
}

interface RunApiRow {
  id: string;
  submission_id?: string | null;
  triggered_by?: string | null;
  run_number?: number | null;
  is_rerun?: boolean | null;
  trigger_source?: string | null;
  status?: string | null;
  degraded_reason?: string | null;
  duration_ms?: number | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  total_cost_usd?: number | null;
}

interface SessionApiRow {
  id: string;
  username?: string | null;
  user_id?: string | null;
  ip?: string | null;
  login_at?: string | null;
  last_seen_at?: string | null;
  duration_seconds?: number | null;
  status?: string | null;
}

interface AuditEventApiRow {
  id: string;
  created_at?: string | null;
  actor_role?: string | null;
  actor_user_id?: string | null;
  event_type: string;
  target_type?: string | null;
  target_id?: string | null;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
}

/* ---------- super-admin console ----------
 * The backend (admin_console.py) wraps rows in {users|documents|runs|sessions|events:[...]}
 * and uses DB-native field names (submission_id, total_cost_usd, prompt_tokens, ...).
 * These wrappers unwrap + map each response onto the UI's row types so the pages
 * (typed against ./types) render real values. */
export const listUsers = async (): Promise<UserRow[]> => {
  const r = await jsonFetch<{ users: AdminUserApiRow[] }>(`${base()}/super_admin/users`);
  return (r.users || []).map((u) => ({
    username: u.username,
    role: u.role,
    registered_ip: u.registered_ip ?? "",
    status: u.is_active ? "active" : "disabled",
    last_login: u.last_login_at ?? undefined,
    run_count: u.runs ?? 0,
    total_cost: Number(u.total_cost_usd ?? 0),
  }));
};
export const createUser = (b: {
  username: string;
  password: string;
  registered_ip?: string | null;
  role?: string;
}) => jsonFetch(`${base()}/super_admin/users`, { method: "POST", body: JSON.stringify(b) });
export const usageSummary = async (q: string = ""): Promise<UsageSummary> => {
  const r = await jsonFetch<{ users: UsageUserApiRow[] }>(`${base()}/super_admin/usage/summary?${q}`);
  const users = r.users || [];
  const total_cost = users.reduce((s, u) => s + Number(u.total_cost_usd ?? 0), 0);
  const total_tokens_in = users.reduce((s, u) => s + (u.input_tokens ?? 0), 0);
  const total_tokens_out = users.reduce((s, u) => s + (u.output_tokens ?? 0), 0);
  const runs = users.reduce((s, u) => s + (u.runs ?? 0), 0);
  return {
    total_cost,
    total_tokens_in,
    total_tokens_out,
    active_users: users.length,
    runs,
    avg_cost_per_run: runs ? total_cost / runs : 0,
  };
};
export const usageByDocument = async (q: string = ""): Promise<DocUsageRow[]> => {
  const r = await jsonFetch<{ documents: DocumentUsageApiRow[] }>(`${base()}/super_admin/usage/by-document?${q}`);
  return (r.documents || []).map((d) => ({
    document_id: d.submission_id,
    title: d.title ?? "",
    graded_by: d.graded_by ?? "",
    input_tokens: d.input_tokens ?? 0,
    output_tokens: d.output_tokens ?? 0,
    cost: Number(d.total_cost_usd ?? 0),
    runs: d.total_runs ?? 0,
    last_run: d.last_run ?? "",
  }));
};
export const listRuns = async (q: string = ""): Promise<RunRow[]> => {
  const r = await jsonFetch<{ runs: RunApiRow[] }>(`${base()}/super_admin/runs?${q}`);
  return (r.runs || []).map((x) => ({
    id: x.id,
    document_title: x.submission_id ?? "",
    user: x.triggered_by ?? "",
    run_number: x.run_number ?? 0,
    is_rerun: !!x.is_rerun,
    trigger: x.trigger_source ?? "",
    status: x.status ?? "",
    degraded_reason: x.degraded_reason ?? undefined,
    duration_ms: x.duration_ms ?? 0,
    input_tokens: x.prompt_tokens ?? 0,
    output_tokens: x.completion_tokens ?? 0,
    cost: Number(x.total_cost_usd ?? 0),
  }));
};
export const listSessions = async (q: string = ""): Promise<SessionRow[]> => {
  const r = await jsonFetch<{ sessions: SessionApiRow[] }>(`${base()}/super_admin/sessions?${q}`);
  return (r.sessions || []).map((s) => ({
    id: s.id,
    user: s.username ?? s.user_id ?? "",
    ip: s.ip ?? "",
    login_time: s.login_at ?? "",
    last_seen: s.last_seen_at ?? "",
    duration_seconds: s.duration_seconds ?? 0,
    status: s.status ?? "",
  }));
};
export const auditFeed = async (q: string = ""): Promise<AuditRow[]> => {
  const r = await jsonFetch<{ events: AuditEventApiRow[] }>(`${base()}/super_admin/audit?${q}`);
  return (r.events || []).map((e) => ({
    id: e.id,
    timestamp: e.created_at ?? "",
    actor: e.actor_role
      ? `${e.actor_role}${e.actor_user_id ? " (" + String(e.actor_user_id).slice(0, 8) + ")" : ""}`
      : (e.actor_user_id ?? "system"),
    event_type: e.event_type,
    target: e.target_type ? `${e.target_type}:${e.target_id ?? ""}` : undefined,
    details: e.metadata ?? {},
  }));
};
export const ruleAudit = async (q: string = ""): Promise<RuleAuditRow[]> => {
  const r = await jsonFetch<{ events: AuditEventApiRow[] }>(`${base()}/super_admin/rules/audit?${q}`);
  return (r.events || []).map((e) => {
    const metadataRuleId =
      typeof e.metadata?.rule_id === "string" ? e.metadata.rule_id : "";
    return {
      id: e.id,
      timestamp: e.created_at ?? "",
      actor: e.actor_role ?? e.actor_user_id ?? "",
      rule_id: e.target_id ?? metadataRuleId,
      before: e.before ?? {},
      after: e.after ?? {},
    };
  });
};

/* ---------- admin: corpus layers ----------
 * All routes gated on `rules:write` (admin + super_admin) — a plain user gets 403. */
export const listCorpusLayers = () => jsonFetch<CorpusLayerList>(`${base()}/admin/corpus/layers`);

/** Enable/disable is one boolean UPDATE — retrieval sees it on the next query
 * and nothing is ever re-embedded. */
export const updateCorpusLayer = (id: string, b: { enabled?: boolean; description?: string }) =>
  jsonFetch<CorpusLayer>(`${base()}/admin/corpus/layers/${id}`, {
    method: "PATCH",
    body: JSON.stringify(b),
  });

/** purge=false unlinks the layer and keeps its precedents (they revert to
 * always-retrieved). purge=true DELETEs the rows — and the row IS the vector,
 * so the embeddings go with them. Irreversible. */
export const deleteCorpusLayer = (id: string, purge: boolean = false) =>
  jsonFetch<CorpusLayerDeleteResult>(
    `${base()}/admin/corpus/layers/${id}?purge=${purge}`,
    { method: "DELETE" }
  );

export const listCorpusLayerItems = (id: string, limit: number = 50, offset: number = 0) =>
  jsonFetch<CorpusLayerItems>(
    `${base()}/admin/corpus/layers/${id}/items?limit=${limit}&offset=${offset}`
  );

/* Per-document curation. A corpus is kept current one source document at a
 * time, so these are the finer grain beneath enable/purge. Deleting removes the
 * embedding in the same statement — the vector is a column on the deleted row,
 * so there is no re-index step and retrieval stops seeing it immediately. */
export const listCorpusLayerDocuments = (id: string) =>
  jsonFetch<{ documents: CorpusLayerDocument[] }>(
    `${base()}/admin/corpus/layers/${id}/documents`
  );

/* Corpus-wide. Rows ingested before layers existed have source_layer_id NULL,
 * so the layer-scoped routes reach none of them — in a mature corpus that is
 * most of the rows. These operate on documents regardless of layer. */
export const listCorpusDocuments = () =>
  jsonFetch<{ documents: CorpusLayerDocument[] }>(`${base()}/admin/corpus/documents`);

/** `corpus` confines the delete to the one table the clicked row represents.
 * The listing is per (corpus, document), so omitting it would delete a
 * same-named document out of every corpus at once — further than the row shows. */
export const deleteCorpusDocument = (sourceFile: string, corpus?: string) =>
  jsonFetch<{ source_file: string; precedents_deleted: number }>(
    `${base()}/admin/corpus/documents?source_file=${encodeURIComponent(sourceFile)}` +
      (corpus ? `&corpus=${encodeURIComponent(corpus)}` : ""),
    { method: "DELETE" }
  );

/** Categorise one uncategorised source document into a layer. Adopts only
 * rows that belong to no layer yet. */
export const claimCorpusLayerDocument = (id: string, sourceFile: string, corpus?: string) =>
  jsonFetch<{ layer_id: string; source_file: string; precedents_claimed: number }>(
    `${base()}/admin/corpus/layers/${id}/documents?source_file=${encodeURIComponent(sourceFile)}` +
      (corpus ? `&corpus=${encodeURIComponent(corpus)}` : ""),
    { method: "POST" }
  );

export const deleteCorpusLayerDocument = (id: string, sourceFile: string, corpus?: string) =>
  jsonFetch<{ layer_id: string; source_file: string; precedents_deleted: number }>(
    `${base()}/admin/corpus/layers/${id}/documents?source_file=${encodeURIComponent(sourceFile)}` +
      (corpus ? `&corpus=${encodeURIComponent(corpus)}` : ""),
    { method: "DELETE" }
  );

/* ---------- admin: retrieval inspector ----------
 * Each of these can legitimately answer {status:"no_retrieval_data", reason}
 * instead of a story — that is a real state, not an error. Discriminate on
 * `.status` before reading anything else. */
export const getRunRetrieval = (runId: string) =>
  jsonFetch<RetrievalInspection>(`${base()}/admin/retrieval/runs/${runId}`);

export const getLatestSubmissionRetrieval = (submissionId: string) =>
  jsonFetch<RetrievalInspection>(`${base()}/admin/retrieval/submissions/${submissionId}/latest`);

export const getRunRejectedRetrieval = (runId: string) =>
  jsonFetch<RetrievalRejectedInspection>(`${base()}/admin/retrieval/runs/${runId}/rejected`);

/* Per-candidate trace (migration 0037). Runs that predate it answer
 * {status:"no_retrieval_data"} here while the three routes above still work —
 * discriminate on `.status`, never on an empty `rows` array. */
export const getRunChunks = (runId: string) =>
  jsonFetch<RetrievalChunksInspection>(`${base()}/admin/retrieval/runs/${runId}/chunks`);

export const getRunCandidates = (
  runId: string,
  params?: {
    chunk_id?: string;
    corpus?: string;
    verdict?: string;
    final_status?: string;
    limit?: number;
    offset?: number;
  }
) => {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v !== undefined && v !== null && v !== "") qs.set(k, String(v));
  }
  const q = qs.toString();
  return jsonFetch<RetrievalCandidatesInspection>(
    `${base()}/admin/retrieval/runs/${runId}/candidates${q ? `?${q}` : ""}`
  );
};


// ------------------------------------------------------------- assignments
// Review buckets + the action trail. See
// docs/superpowers/specs/2026-08-19-review-buckets-and-trail-design.md

export async function listMyBucket(): Promise<{ assignments: ReviewAssignment[]; total: number }> {
  return jsonFetch(`${base()}/assignments/my`);
}

export async function listAssignments(
  params: { status?: AssignmentStatus; assignee_id?: string } = {}
): Promise<{ assignments: ReviewAssignment[]; total: number }> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.assignee_id) qs.set("assignee_id", params.assignee_id);
  const suffix = qs.toString() ? `?${qs}` : "";
  return jsonFetch(`${base()}/assignments${suffix}`);
}

export async function assignSubmission(body: {
  submission_id: string;
  assignee_id: string;
  priority?: AssignmentPriority;
  due_at?: string | null;
  note?: string | null;
}): Promise<ReviewAssignment> {
  return jsonFetch(`${base()}/assignments`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function reassignAssignment(
  id: string,
  body: { assignee_id: string; note?: string | null }
): Promise<ReviewAssignment> {
  return jsonFetch(`${base()}/assignments/${id}/reassign`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function startAssignment(id: string): Promise<ReviewAssignment> {
  return jsonFetch(`${base()}/assignments/${id}/start`, { method: "POST" });
}

export async function completeAssignment(id: string): Promise<ReviewAssignment> {
  return jsonFetch(`${base()}/assignments/${id}/complete`, { method: "POST" });
}

export async function sendBackAssignment(id: string, reason: string): Promise<ReviewAssignment> {
  return jsonFetch(`${base()}/assignments/${id}/send-back`, {
    method: "POST",
    body: JSON.stringify({ reason }),
  });
}

export async function cancelAssignment(id: string, reason?: string): Promise<ReviewAssignment> {
  return jsonFetch(`${base()}/assignments/${id}/cancel`, {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

/** Sign off an assignment and end it. Approving the document does this
 *  implicitly; this is the explicit path, chiefly for `rejected`. */
export async function closeAssignment(
  id: string,
  outcome: AssignmentOutcome = "approved",
  note?: string,
): Promise<ReviewAssignment> {
  return jsonFetch(`${base()}/assignments/${id}/close`, {
    method: "POST",
    body: JSON.stringify({ outcome, note: note ?? null }),
  });
}

export async function assignmentWorkload(): Promise<{ reviewers: WorkloadRow[] }> {
  return jsonFetch(`${base()}/assignments/workload`);
}

export async function assignmentForSubmission(
  submissionId: string
): Promise<{ active: ReviewAssignment | null; history: ReviewAssignment[] }> {
  return jsonFetch(`${base()}/assignments/for-submission/${submissionId}`);
}

export async function documentTrail(submissionId: string): Promise<{ trail: TrailRow[] }> {
  return jsonFetch(`${base()}/assignments/trail/document/${submissionId}`);
}

export async function reviewerTrail(userId: string): Promise<ReviewerTrail> {
  return jsonFetch(`${base()}/assignments/trail/reviewer/${userId}`);
}
