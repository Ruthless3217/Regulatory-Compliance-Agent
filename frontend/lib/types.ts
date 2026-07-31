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
  // 0025 — editable working copy; NULL means "no edits yet, current ==
  // original". original_content stays immutable (grading/highlighting
  // reference); current_content is what edits/apply-fix/restore write.
  current_content?: string | null;
  file_path?: string | null;
  submitted_by?: string | null;
  submitted_at?: string;
  status: SubmissionStatus;
  approval_status?: string;
  // 0024 — async page-render pass status, independent of `status` (the
  // analysis lifecycle). PDF-only; null/"skipped" for every other
  // content_type. Drives the PdfPagePane vs. text-mark DocumentPane switch.
  page_render_status?: RenderStatus | null;
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
  product_line?: string | null;
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
  // Rule-citation locators (rule path) — previously dropped by both hand-rolled
  // serializers despite being populated at write time.
  cited_section?: string | null;
  cited_page?: number | null;
  cited_regulation_version?: string | null;
  rule_version?: number | null;
  // 0023 — which AnalysisRun produced this finding, and its reviewer-facing
  // lifecycle (open/actioned) independent of the rule_feedback audit trail.
  analysis_run_id?: string | null;
  review_status?: string | null;
  resolved_at?: string | null;
  // 0024 — real page/bbox anchor for the document viewer, matching
  // pdf_render_service.PositionedWord ([x0,y0,x1,y1]).
  section_title?: string | null;
  anchor_page?: number | null;
  anchor_bbox?: [number, number, number, number] | null;
  // 0028 — has this finding's suggested_fix already been written into the
  // document via a submission_revisions entry.
  fix_applied?: boolean | null;
  fix_applied_at?: string | null;
  // 0031 — authorship. "model" = an analysis run produced this finding;
  // "reviewer" = a human flagged text the model never surfaced. Only
  // reviewer-authored flags are deletable, and only they are kept out of the
  // model-precision math on /model-learning. Absent => treat as "model".
  source?: "model" | "reviewer" | string;
  created_by?: string | null;
  created_by_username?: string | null;
  // Left-joined latest reviewer verdict (rule_feedback), if any. Legacy rows
  // may still hold "accept"/"reject"; new rows hold the reviewer-action
  // taxonomy value ("correct" | "not_violation" | "dismiss").
  reviewer_verdict?: string | null;
  reviewer_comment?: string | null;
}

/** The reviewer-action taxonomy (Correct / Not-a-violation / Dismiss) —
 * POST /compliance/violations/{id}/actions. Replaces the old binary
 * accept/reject shim in the UI (the shim endpoint still exists server-side
 * for back-compat). */
export type ReviewerActionType = "correct" | "not_violation" | "dismiss";

/** Dismiss reason picker — required, no weight-update signal either way. */
export type DismissReason =
  | "duplicate"
  | "vague"
  | "low-value"
  | "insufficient-evidence"
  | "needs-human-legal-review"
  | "unsupported-format"
  | "other";

/** "Not a violation" reason picker — required, paired with a required
 * free-text explanation. */
export type NotViolationReason =
  | "wrong-product"
  | "wrong-section"
  | "wrong-context"
  | "outdated-rule"
  | "retrieval-mismatch"
  | "valid-regulatory-exception"
  | "wrong-severity"
  | "hallucination"
  | "other";

// --- Content revisions (migration 0026) -------------------------------------
// The one mutation primitive behind manual edits, apply-fix, bulk-apply-fixes,
// and restore.
export type RevisionSource = "manual_edit" | "apply_fix" | "bulk_apply_fixes" | "restore";

export interface SubmissionRevision {
  id: string;
  submission_id: string;
  revision_number: number;
  content: string;
  source: RevisionSource;
  note: string | null;
  applied_violation_ids: string[];
  created_by: string | null;
  created_at: string | null;
}

export type ActionType =
  | "rewrite"
  | "share-evidence"
  | "add-disclaimer"
  | "verify-source"
  | "remove";

export interface ViolationMetadata {
  grounding?: "precedent" | "rule" | "novel" | "disclosure" | "product_fact";
  action_type?: ActionType | string;
  evidence_needed?: string | null;
  regulatory_basis?: string | null;
  // Mandatory-disclosure explainability payload (2026-07-28). Present only on
  // grounding === "disclosure" findings.
  verdict_provenance?: "deterministic_rule" | "hybrid" | string;
  match_method?: string;
  match_reason?: string;
  normalized_similarity?: number;
  raw_similarity?: number;
  evidence_span?: string;
  token_overlap?: number;
  critical_tokens_missing?: string[];
  decision_trace?: string[];
  counterfactual?: string;
  approved_wording?: string;
  // Product-fact grounding (nodes.py::_product_fact_finding_to_violation) —
  // which resolved product this finding's fact card belongs to. Present only
  // on grounding === "product_fact" findings; other groundings have no single
  // product to pin (rule/precedent findings can apply doc-wide).
  product_uin?: string;
  product_name?: string;
  [k: string]: unknown;
}

export type ScoreBreakdown = Record<string, number | string>;

export interface FindingCounts {
  scored: number;
  suppressed: number;
  total: number;
}

export interface ComplianceResults {
  submission_id: string;
  check_id?: string;
  overall_score?: number;
  grade?: "A" | "B" | "C" | "D" | "F";
  compliance_status?: string;
  scores?: ScoreBreakdown;
  checked_at?: string | null;
  violations: Violation[];
  violation_count?: number;
  suppressed_count?: number;
  finding_count?: number;
  finding_counts?: FindingCounts;
  status?: SubmissionStatus;
  message?: string;
}

// GET /compliance/check/{check_id} — reviewer-facing summary for one specific
// run's compliance check (keyed by AnalysisRun.compliance_check_id), used to
// show a historical run's own findings instead of the submission's latest.
export interface CheckSummary {
  id: string;
  submission_id: string;
  overall_score: number | null;
  grade: string | null;
  status: string | null;
  scores: ScoreBreakdown | null;
  checked_at: string | null;
  violations: Violation[];
  violation_count?: number;
  suppressed_count?: number;
  finding_count?: number;
  finding_counts?: FindingCounts;
}

// --- Reviewer-facing run history + diff (GET /compliance/submissions/{id}/runs,
// GET /compliance/runs/{id}/diff) --------------------------------------------
export interface RunSummary {
  id: string;
  run_number: number;
  is_rerun: boolean;
  status: string;
  degraded_reason: string | null;
  compliance_check_id: string | null;
  started_at: string | null;
  finished_at: string | null;
  scoring_policy_version: string | null;
}

export interface RunDiff {
  run_id: string;
  against_run_id: string;
  run_number: number;
  against_run_number: number;
  added: Violation[];
  removed: Violation[];
  unchanged_count: number;
  summary: { added: number; removed: number; unchanged: number };
}

// GET/POST /submissions/{id}/comments (migration 0027) — a freestanding
// reviewer note anchored to a text selection.
export interface DocumentComment {
  id: string;
  submission_id: string;
  anchor_text: string | null;
  page_number: number | null;
  body: string;
  resolved: boolean;
  created_by: string | null;
  created_at: string | null;
  updated_at: string | null;
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
  scores: ScoreBreakdown;
}

export interface TimeseriesPoint {
  period: string;
  submission_count: number;
  avg_score: number | null;
  violation_count: number;
  suppressed_count: number;
  finding_count: number;
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
  | { type: "delete"; old_text: string; moved?: boolean; move_id?: string }
  | { type: "insert"; new_text: string; moved?: boolean; move_id?: string }
  | { type: "replace"; old_words: DiffWord[]; new_words: DiffWord[]; moved?: boolean; move_id?: string };

export type RenderBoxType = "removed" | "added";

export interface RenderBox {
  x0: number; y0: number; x1: number; y1: number;
  type: RenderBoxType;
  change_id: string;
}
export interface RenderPage { n: number; w_pt: number; h_pt: number; boxes: RenderBox[]; }
export interface RenderChangeRef { page: number; bbox: [number, number, number, number]; text: string; }
export interface RenderChange {
  id: string;
  kind: ChangeKind;
  old?: RenderChangeRef;
  new?: RenderChangeRef;
}
export interface RenderResult {
  old: { pages: RenderPage[] };
  new: { pages: RenderPage[] };
  changes: RenderChange[];
  truncated_pages: number;
}
export type RenderStatus = "processing" | "completed" | "failed" | "skipped";

export type ChangeKind = "removed" | "added" | "modified" | "moved";

/** A reviewer annotation attached to one change (keyed by its selection id). */
export interface Annotation {
  change_id: string;
  note: string | null;
  tags: string[];
  updated_at: string;
}

/** One in-document search hit returned by the per-side word scan. */
export interface SearchHit {
  page: number;
  bbox: [number, number, number, number];
}

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
  /** Original uploaded file name for each side; null/absent when the side was pasted text. */
  old_filename?: string | null;
  new_filename?: string | null;
  status: ComparisonStatus;
  error_message?: string | null;
  diff_result?: DiffBlock[] | null;
  render_status?: RenderStatus | null;
  render_result?: RenderResult | null;
  render_error?: string | null;
  annotations?: Annotation[];
  created_at: string;
}

export interface Me {
  username: string;
  role: string;
  must_change_password?: boolean;
}

export interface UserRow {
  username: string;
  role: string;
  registered_ip: string;
  status: string;
  last_login?: string;
  run_count: number;
  total_cost: number;
}

export interface UsageSummary {
  total_cost: number;
  total_tokens_in: number;
  total_tokens_out: number;
  active_users: number;
  runs: number;
  avg_cost_per_run: number;
}

export interface DocUsageRow {
  document_id: string;
  title: string;
  graded_by: string;
  input_tokens: number;
  output_tokens: number;
  cost: number;
  runs: number;
  last_run: string;
}

export interface RunRow {
  id: string;
  document_title: string;
  user: string;
  run_number: number;
  is_rerun: boolean;
  trigger: string;
  status: string;
  degraded_reason?: string;
  duration_ms: number;
  input_tokens: number;
  output_tokens: number;
  cost: number;
}

export interface SessionRow {
  id: string;
  user: string;
  ip: string;
  login_time: string;
  last_seen: string;
  duration_seconds: number;
  status: string;
}

export interface AuditRow {
  id: string;
  timestamp: string;
  actor: string;
  event_type: string;
  target?: string;
  details?: Record<string, any>;
}

export interface RuleAuditRow {
  id: string;
  timestamp: string;
  actor: string;
  rule_id: string;
  before: Record<string, any>;
  after: Record<string, any>;
}

export interface RagHealth {
  backend: string;
  embedder: string;
  model: string;
  dim: number;
  embedder_ok: boolean;
  embedder_error: string | null;
  vector_store_ok: boolean;
  last_indexed_at: Record<string, string | null>;
}

export interface ModelsHealth {
  llm_provider: string;
  llm_model: string;
  critic_llm_model: string;
  chat_llm_model: string;
  disclosure_check_enabled: boolean;
  product_grounding_enabled: boolean;
}

/* ---------- model learning ---------- */
export interface LearningFunnel {
  flagged: number;
  awaiting_review: number;
  feedback_collected: number;
  applied_to_scoring: number;
  /** Findings a reviewer wrote by hand — the model's misses. Reported beside
   * the funnel, never inside it, so a human's work never counts as model
   * output. */
  reviewer_authored: number;
  no_gate_warning: string;
  status: "computed";
}

export interface PrecisionGroup {
  key: string;
  correct: number;
  not_violation: number;
  reviewed_total: number;
  precision: number | null;
  status: "computed" | "insufficient_data";
  rule_text?: string | null;
  category?: string | null;
  severity?: string | null;
}

export interface LearningPrecision {
  by: "rule" | "category" | "severity";
  groups: PrecisionGroup[];
  status: "computed" | "insufficient_data";
  note: string;
}

export interface CalibrationPoint {
  check_id: string;
  checked_at: string | null;
  system_score: number;
  reviewer_score: number;
  gap: number;
}

export interface LearningCalibration {
  sample_size: number;
  status: "computed" | "insufficient_data";
  mean_absolute_gap: number | null;
  mean_system_score: number | null;
  mean_reviewer_score: number | null;
  points: CalibrationPoint[];
  note: string;
}

export interface RuleReliabilityEventRow {
  id: string;
  created_at: string | null;
  rule_feedback_id: string | null;
  alpha_before: number;
  beta_before: number;
  theta_before: number;
  alpha_after: number;
  beta_after: number;
  theta_after: number;
}

export interface RuleReliabilityHistory {
  rule_id: string;
  rule_text: string | null;
  current_alpha: number | null;
  current_beta: number | null;
  current_theta: number | null;
  events: RuleReliabilityEventRow[];
  status: "computed" | "insufficient_data";
  note: string;
}

export interface LearningLatency {
  sample_size: number;
  status: "computed" | "insufficient_data";
  avg_duration_ms: number | null;
  p50_duration_ms: number | null;
  p95_duration_ms: number | null;
  avg_prompt_tokens: number | null;
  avg_completion_tokens: number | null;
  avg_cost_usd: number | null;
  by_trigger_source: { trigger_source: string; count: number; avg_duration_ms: number }[];
  note: string;
}

export interface RepeatedPattern {
  rule_id: string;
  rule_text: string | null;
  category: string | null;
  severity: string | null;
  submission_count: number;
  violation_count: number;
  reliability_theta: number | null;
  reviewer_precision: number | null;
  reviewed_count: number;
}

export interface RepeatedPatterns {
  min_submissions: number;
  patterns: RepeatedPattern[];
  status: "computed" | "insufficient_data";
  note: string;
}

/* ---------- admin: corpus layers (backend/app/api/routes/admin_corpus.py) ----------
 * A layer is one ingested contribution with an off switch. `enabled=false` hides
 * its precedents from retrieval on the next query and re-enabling costs nothing —
 * the embeddings never move. Deleting is the separate, destructive operation. */

export interface CorpusLayer {
  id: string;
  name: string;
  kind: string;
  description: string | null;
  enabled: boolean;
  source_ref: string | null;
  ingested_by: string | null;
  item_count: number;
  created_at: string | null;
  updated_at: string | null;
}

export interface CorpusLayerList {
  layers: CorpusLayer[];
  /** Precedents belonging to no layer (the pre-0030 corpus). Always retrieved;
   * they have no provenance and cannot be switched off. */
  unlayered_count: number;
  kinds: string[];
}

export interface CorpusLayerItem {
  id: string;
  highlighted_span: string | null;
  reviewer_comment: string | null;
  issue_type: string | null;
  severity: string | null;
  product_category: string | null;
  ticket: string | null;
  source_file: string | null;
  occurrence_count: number | null;
}

export interface CorpusLayerItems {
  total: number;
  limit: number;
  offset: number;
  items: CorpusLayerItem[];
}

export interface CorpusLayerDeleteResult {
  id: string;
  name: string;
  purged: boolean;
  precedents_deleted: number;
  precedents_orphaned: number;
}

/* ---------- admin: retrieval inspector (backend/app/api/routes/admin_retrieval.py) ---- */

export interface RetrievalScope {
  uins: string[];
  categories: string[];
  /** false => no product identified, so nothing could be rejected on scope (C3). */
  resolved: boolean;
}

export interface ProductMatch {
  uin: string;
  product_name: string;
  confidence: number;
  method: string;
  ambiguous: boolean;
  candidates: string[];
}

/** The enriched referent behind a candidate id. `rules` rows carry rule_text /
 * category; `precedents` rows carry issue_type / highlighted_span / source_file.
 * null when the lookup failed or the row is gone — see `enrichment_notes`. */
export interface RetrievalDocument {
  rule_text?: string | null;
  category?: string | null;
  product_line?: string | null;
  issue_type?: string | null;
  highlighted_span?: string | null;
  source_file?: string | null;
  product_category?: string | null;
  severity?: string | null;
}

export interface RetrievalCandidate {
  corpus: string;
  id: string;
  score: number | null;
  /** The candidate's own scope tag (rules.product_line / precedent product_category). */
  scope_value: string | null;
  verdict: "accepted" | "rejected" | string;
  reason: string | null;
  tier?: string | null;
  chunk_id?: string | null;
  document?: RetrievalDocument | null;
}

export interface RetrievalRunHead {
  run_id: string;
  submission_id: string;
  run_number: number | null;
  run_status: string | null;
  degraded_reason: string | null;
  started_at: string | null;
}

/** Runs predating migration 0022, runs that died before dispatch, and runs with
 * zero candidates all land here with a distinct `reason`. NEVER render this as
 * an empty table — an empty table reads as "nothing was rejected". */
export interface RetrievalNoData extends RetrievalRunHead {
  status: "no_retrieval_data";
  reason: string;
}

export interface RetrievalTotals {
  candidates_total: number | null;
  rejected_total: number | null;
  records_available: number;
  /** true => the persisted acceptances are a 100-cap SAMPLE, not the population. */
  truncated: boolean;
  note: string | null;
  recorded_rejected: number;
}

export interface RetrievalStory extends RetrievalRunHead {
  status: "ok";
  scope: RetrievalScope | null;
  product_match: ProductMatch[] | null;
  degraded: Record<string, unknown>;
  totals: RetrievalTotals;
  by_corpus_recorded: Record<string, { accepted: number; rejected: number }>;
  /** corpus -> tier -> candidates */
  candidates: Record<string, Record<string, RetrievalCandidate[]>>;
  enrichment_notes: Record<string, string> | null;
}

export interface RetrievalRejectedStory extends RetrievalRunHead {
  status: "ok";
  scope: RetrievalScope | null;
  rejected_total: number | null;
  records_available: number;
  /** reason code -> bucket. Rejections are complete, never sampled. */
  by_reason: Record<string, { count: number; candidates: RetrievalCandidate[] }>;
  enrichment_notes: Record<string, string> | null;
}

export type RetrievalInspection = RetrievalStory | RetrievalNoData;
export type RetrievalRejectedInspection = RetrievalRejectedStory | RetrievalNoData;
