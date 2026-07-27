export type Severity = "critical" | "high" | "medium" | "low";
export type Category = "irdai" | "brand" | "sebi" | "regulatory" | "seo";
export type SubmissionStatus =
  | "uploaded"
  | "preprocessing"
  | "preprocessed"
  | "analyzing"
  | "analyzed"
  | "failed"
  | "waiting_for_review";

export interface Submission {
  id: string;
  title: string;
  content_type: string;
  original_content?: string | null;
  file_path?: string | null;
  submitted_by?: string | null;
  submitted_at?: string;
  status: SubmissionStatus;
  approval_status?: string;
}

export interface Rule {
  id: string;
  category: Category | string;
  rule_text: string;
  severity: Severity | string;
  is_active: boolean;
  points_deduction?: number;
  is_auto_generated?: boolean;
  keywords?: string[] | null;
  created_at?: string;
}

export interface Violation {
  id: string;
  category: Category | string;
  severity: Severity | string;
  description: string;
  location?: string | null;
  current_text?: string | null;
  suggested_fix?: string | null;
  auto_fixable: string | boolean;
  chunk_index?: number | null;
  rule_id?: string | null;
  // P1.3 — LLM-reported confidence + verbatim regulator citation
  confidence?: number | null;
  regulator_quote?: string | null;
  // Reviewer-voice tags (2026-05-28). Carried in JSONB; render as a badge row.
  violation_metadata?: ViolationMetadata | null;
  // Precedent-citation provenance (Phase 1.5). Populated only on the precedent path.
  cited_precedent_id?: string | null;
  cited_document_id?: string | null;
  cited_source_file?: string | null;
  cited_anchor_text?: string | null;
  cited_comment_verbatim?: string | null;
  // The approved rewrite a past reviewer applied to the matching precedent — the
  // single best answer to "how was this fixed before". Serialized by the API but
  // previously dropped at this boundary (full-pipeline audit 2026-06-16).
  cited_final_text?: string | null;
  // Precedent match strength (cosine) — lets the user gauge how close the cited
  // past case really is.
  similarity_score?: number | null;
  // Sub-confidence-floor / structural findings: kept out of the score and shown
  // in a separate "Needs review" lane (recall fix 2026-06-08).
  suppressed?: boolean | null;
  suppressed_reason?: string | null;
}

export type ActionType =
  | "rewrite"
  | "share-evidence"
  | "add-disclaimer"
  | "verify-source"
  | "remove";

export interface ViolationMetadata {
  grounding?: "precedent" | "rule" | "novel";
  action_type?: ActionType | string;
  evidence_needed?: string | null;
  regulatory_basis?: string | null;
  [k: string]: unknown;
}

export interface ComplianceResults {
  submission_id: string;
  check_id?: string;
  overall_score?: number;
  grade?: "A" | "B" | "C" | "D" | "F";
  compliance_status?: string;
  scores?: Record<string, number>;
  checked_at?: string | null;
  violations: Violation[];
  violation_count?: number;
  status?: SubmissionStatus;
  message?: string;
}

export interface DashboardSummary {
  total_submissions?: number;
  total_violations?: number;
  avg_score?: number;
  auto_fix_rate?: number;
  this_week?: number;
  [k: string]: unknown;
}

export interface SSEAnalyzeStage {
  stage: "preprocess" | "dispatch" | "analysis" | "scoring" | "error";
  progress: number;
}

export interface SSEAnalyzeChunk {
  chunk_index: number;
  category: string;
  violations: Violation[];
}

export interface SSEAnalyzeScore {
  overall_score: number;
  grade: string;
  scores: Record<string, number>;
}

export interface TimeseriesPoint {
  period: string;
  submission_count: number;
  avg_score: number | null;
  violation_count: number;
}

export interface TimeseriesResponse {
  bucket: "day" | "week" | string;
  points: TimeseriesPoint[];
}

export interface TopRule {
  rule_id: string;
  category: Category | string;
  severity: Severity | string;
  rule_text: string | null;
  count: number;
}

export interface TopRulesResponse {
  top_rules: TopRule[];
}

export interface PrecedentHitFields {
  reviewer_name?: string | null;
  comment_text?: string | null;
  chunk_text?: string | null;
  anchor_text?: string | null;
  final_text_chunk?: string | null;
  violation_category?: string | null;
  severity?: string | null;
  document_id?: string | null;
  source_file?: string | null;
  [k: string]: unknown;
}

export interface PrecedentHit {
  id: string;
  score: number;
  fields: PrecedentHitFields;
}

export interface KnowledgeBaseSearchResponse {
  query: string;
  results: PrecedentHit[];
}

export interface ProjectionPoint {
  id: string;
  index: "rag_compliance_examples" | "rag_rules" | "rag_source_docs" | string;
  x: number;
  y: number;
  category?: string | null;
  severity?: string | null;
  reviewer_name?: string | null;
  label?: string | null;
  snippet?: string | null;
}

export interface ProjectionResponse {
  method: "umap" | "pca" | string;
  computed_at: string;
  counts: Record<string, number>;
  points: ProjectionPoint[];
}

export type ComparisonStatus = "processing" | "completed" | "failed";

export interface DiffWord {
  text: string;
  changed: boolean;
}

export type DiffBlock =
  | { type: "equal"; old_text: string; new_text: string }
  | { type: "delete"; old_text: string }
  | { type: "insert"; new_text: string }
  | { type: "replace"; old_words: DiffWord[]; new_words: DiffWord[] };

export type ChangeKind = "removed" | "added" | "modified";

/**
 * A single entry in the Compare "Changes" sidebar, derived from one non-equal
 * DiffBlock. `id` matches the block's index in the diff so the viewer can scroll
 * to it. For "modified", removedText/addedText hold only the changed words.
 */
export interface ChangeItem {
  id: string;
  blockIndex: number;
  kind: ChangeKind;
  removedText?: string;
  addedText?: string;
}

export interface DocumentComparison {
  id: string;
  title: string;
  old_content_type: string;
  new_content_type: string;
  status: ComparisonStatus;
  error_message?: string | null;
  diff_result?: DiffBlock[] | null;
  created_at: string;
}

/* ---------- auth & RBAC (Phase 2) ---------- */

export type Role = "user" | "admin" | "super_admin";

/** The authenticated principal, as returned by `GET /auth/me`. */
export interface Me {
  id: string;
  username: string;
  role: Role;
  must_change_password: boolean;
}

/** The minimal payload `POST /auth/login` returns on success (200). */
export interface LoginResult {
  role: Role;
  must_change_password: boolean;
}

/* ---------- super-admin console (Phase 6) ----------
 *
 * Shapes mirror the SQL rollups in audit-trail/02 §7 and the models in
 * backend/app/models/{analysis_run,user_session,llm_usage_event,audit_event}.py.
 * Numeric money columns (Postgres NUMERIC) may serialize as strings, so every
 * console formatter coerces through Number(...) before rendering.
 */

/** One row of `GET /super_admin/users` — account + status + activity rollup. */
export interface UserRow {
  id: string;
  username: string | null;
  role: Role;
  registered_ip: string | null;
  is_active: boolean;
  must_change_password?: boolean;
  last_login_at?: string | null;
  created_at?: string | null;
  /** Lifetime (or period) analysis runs attributed to this user. */
  runs?: number;
  /** Lifetime (or period) spend attributed to this user, USD. */
  total_cost_usd?: number | string | null;
}

/** One row of `GET /super_admin/usage/summary` — per-user tokens + cost. */
export interface UsageSummaryRow {
  user_id?: string | null;
  username: string | null;
  role?: Role | string | null;
  input_tokens: number;
  output_tokens: number;
  total_cost_usd: number | string;
  runs: number;
  // Not returned by the backend summary rollup; the page falls back to
  // input+output for the total and shows "—" for sessions.
  total_tokens?: number;
  sessions?: number;
}

/** One row of `GET /super_admin/usage/by-document` — per-submission rollup. */
export interface DocUsageRow {
  submission_id: string;
  title: string;
  graded_by: string | null;
  input_tokens: number;
  output_tokens: number;
  total_cost_usd: number | string;
  /** MAX(run_number) — total runs incl. re-runs for this document. */
  total_runs: number;
  last_run?: string | null;
}

/** One point of `GET /super_admin/usage/timeseries` — daily cost/tokens. */
export interface UsageTimeseriesPoint {
  day: string;
  total_tokens: number;
  total_cost_usd: number | string;
}

/**
 * One row of the `per_user_active_time` rollup returned alongside
 * `GET /super_admin/sessions`. Not currently rendered (the sessions page reads
 * only the `sessions` array), but typed so the response contract is complete.
 */
export interface PerUserActiveTime {
  username: string | null;
  sessions: number;
  active_seconds: number;
}

/** One row of `GET /super_admin/runs` (and `/submissions/{id}/runs`). */
export interface RunRow {
  id: string;
  submission_id: string;
  submission_title?: string | null;
  triggered_by?: string | null;
  username?: string | null;
  session_id?: string | null;
  run_number: number;
  is_rerun: boolean;
  trigger_source: string; // sync | async | stream
  status: string; // running | completed | needs_review | failed
  compliance_check_id?: string | null;
  degraded_reason?: string | null;
  started_at: string;
  finished_at?: string | null;
  duration_ms?: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  total_tokens: number;
  total_cost_usd: number | string;
}

/** One row of `GET /super_admin/sessions` — login session for session-time. */
export interface SessionRow {
  id: string;
  user_id?: string | null;
  username?: string | null;
  ip?: string | null;
  user_agent?: string | null;
  login_at: string;
  last_seen_at?: string | null;
  logout_at?: string | null;
  duration_seconds?: number | null;
  status: string; // active | closed | expired
}

/** One row of `GET /super_admin/audit` — an append-only audit event. */
export interface AuditRow {
  id: string;
  event_type: string;
  actor_user_id?: string | null;
  actor_username?: string | null;
  actor_role?: string | null;
  actor_ip?: string | null;
  session_id?: string | null;
  target_type?: string | null;
  target_id?: string | null;
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
  created_at: string;
}

/** One row of `GET /super_admin/rules/audit` — a rule-change timeline entry. */
export interface RuleAuditRow {
  id: string;
  event_type: string; // rule_created | rule_updated | rule_deactivated | rule_deleted
  actor_user_id?: string | null;
  actor_username?: string | null;
  actor_role?: string | null;
  target_id?: string | null; // rule id
  before?: Record<string, unknown> | null;
  after?: Record<string, unknown> | null;
  metadata?: Record<string, unknown> | null;
  created_at: string;
}
