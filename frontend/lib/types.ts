export type Severity = "critical" | "high" | "medium" | "low";
export type Category = "irdai" | "brand" | "sebi" | "regulatory" | "seo";
export type SubmissionStatus =
  | "uploaded"
  | "preprocessing"
  | "preprocessed"
  | "analyzing"
  | "analyzed"
  | "failed"
  | "needs_review"
  | "waiting_for_review";

export interface Submission {
  id: string;
  title: string;
  content_type: string;
  product_line?: string | null;
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
  /** Working document. Authoritative once present; the uploaded file stays
   * immutable and is kept only as the original for audit. */
  lexical_state?: Record<string, unknown> | null;
  /** True when an import source exists to seed the editor from. The HTML
   * itself is NOT here — converting a long document costs seconds, so it is
   * fetched from /import-html only when the editor actually needs it. */
  has_import_source?: boolean;
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
  // Editable-document anchors. `anchor_node_key` is a CONTENT-derived block id
  // (sha1 of the block's normalized text — see components/editor/sectionMap.ts),
  // not a Lexical node key: node keys exist only inside one browser tab, so
  // nothing the backend writes could ever match one. Offsets are into the
  // block's normalized text. `anchor_fingerprint` is the surrounding text the
  // analysis saw, kept so a relocation can be argued for after the span itself
  // is edited.
  //
  // All optional and all absent today — the backend does not write them yet.
  // Declared here rather than read off untyped JSON so the day it does, the
  // shape it must produce is stated in one place. findingAnchor.ts degrades to
  // a document-wide text search when they are missing, which is what it did
  // before they existed.
  anchor_node_key?: string | null;
  anchor_offset_start?: number | null;
  anchor_offset_end?: number | null;
  anchor_fingerprint?: string | null;
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
  reviewer_added: number;
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
  reviewer_added_count?: number;
  finding_count?: number;
  finding_counts?: FindingCounts;
  // Document was edited after this analysis: these findings describe a
  // superseded version, so export is blocked until it is re-run.
  findings_stale?: boolean;
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
  reviewer_added_count?: number;
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
  check_count: number;
  avg_score: number | null;
  violation_count: number;
  suppressed_count: number;
  reviewer_added_count: number;
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
  /** GET /auth/me has always returned this; the type just never declared it.
   *  Needed to tell "my assignment" from someone else's. */
  id: string;
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
  details?: Record<string, unknown>;
}

export interface RuleAuditRow {
  id: string;
  timestamp: string;
  actor: string;
  rule_id: string;
  before: Record<string, unknown>;
  after: Record<string, unknown>;
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

/** One physical table corpus layers govern (backend services/rag/corpus_registry). */
export interface CorpusRef {
  table: string;
  label: string;
}

export interface CorpusLayer {
  id: string;
  name: string;
  kind: string;
  description: string | null;
  enabled: boolean;
  source_ref: string | null;
  ingested_by: string | null;
  item_count: number;
  /** Which corpora this layer's switch actually governs. A `precedent_ingest`
   * layer spans two tables; "disable" means nothing to an admin who cannot see
   * its blast radius. */
  corpora: CorpusRef[];
  created_at: string | null;
  updated_at: string | null;
}

export interface CorpusLayerList {
  layers: CorpusLayer[];
  /** Rows belonging to no layer (the corpus that predates layers). Always
   * retrieved; they have no provenance and cannot be switched off. */
  unlayered_count: number;
  /** The same total per corpus. "0 layered, 5,229 unlayered legacy precedents"
   * is the honest read of a v1 corpus, and the page has to be able to say it. */
  unlayered_by_corpus: { corpus: string; label: string; count: number }[];
  kinds: string[];
  corpora: CorpusRef[];
  /** kind -> the tables a layer of that kind governs. */
  kind_corpora: Record<string, string[]>;
}

export interface CorpusLayerItem {
  id: string;
  /** Which physical table this row came from. */
  corpus: string;
  corpus_label: string;
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

/** One source document's contribution to a layer. The admin curates in
 * documents ("drop the 2019 brochure"), not in individual reviewer comments. */
export interface CorpusLayerDocument {
  /** Rows are per (corpus, document), not per filename: the same name can exist
   * in two corpora and one Delete click must not reach further than it shows. */
  corpus: string;
  corpus_label: string;
  source_file: string | null;
  precedent_count: number;
  /** How many of this document's precedents belong to no layer. Only ever
   * non-zero in the corpus-wide listing — that is where uncategorised rows
   * become visible and assignable. */
  unlayered_count: number;
  last_updated: string | null;
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
  /** false => neither product identity nor a valid declaration resolved scope. */
  resolved: boolean;
  declared_product_line?: string | null;
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
  /** Exception class name, when the absence was a failed lookup rather than an
   * empty one (e.g. the trace table does not exist yet). */
  detail?: string;
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

/* ---------- per-candidate trace (migration 0037) ----------
 * The three types above read a 100-row SAMPLE out of run_metadata. These read
 * `retrieval_candidates`: every candidate of every (chunk, category) query,
 * with the per-leg scores the store used to discard and the stage that decided
 * each one. Runs predating 0037 have no rows and answer with RetrievalNoData —
 * the sampled views above still work for them. */

/** What decided this candidate's fate. Vocabulary owned by
 * backend/app/services/rag/trace.py (_FINAL_STATUS_DOC). */
export type RetrievalFinalStatus =
  | "in_prompt"
  | "dropped_by_cap"
  | "unused_fallback"
  | "rejected_applicability"
  | "below_threshold"
  | "dropped_by_retriever"
  | "not_retrieved_far_enough";

export interface RetrievalTraceRow {
  chunk_id: string | null;
  corpus: string;
  tier: string | null;
  category: string | null;
  candidate_id: string;
  /** Alias of candidate_id, so a traced row renders through the same helpers
   * as a sampled RetrievalCandidate. */
  id: string;
  /** Which leg found it. "both" = semantically AND lexically. */
  retrieval_method: "vector" | "bm25" | "both" | null;
  cosine: number | null;
  ts_rank: number | null;
  vector_rank: number | null;
  bm25_rank: number | null;
  fused_score: number | null;
  fused_rank: number | null;
  filters: { index?: string; applied?: Record<string, unknown> | null } | null;
  scope_value: string | null;
  /** null means the applicability judge never saw this row — it lost the
   * fusion, or a retriever filter dropped it first. NOT "accepted". */
  verdict: "accepted" | "rejected" | null;
  reason: string | null;
  final_status: RetrievalFinalStatus | string;
  deciding_stage: string;
  /** Computed server-side by joining the run's violations. */
  used_in_final_verdict: boolean;
  document?: RetrievalDocument | null;
}

export interface RetrievalCandidatesPage extends RetrievalRunHead {
  status: "ok";
  /** Rows traced for the whole run, before filters. */
  total_traced: number;
  /** Rows matching the current filters. */
  matched: number;
  limit: number;
  offset: number;
  filters: {
    chunk_id: string | null;
    corpus: string | null;
    verdict: string | null;
    final_status: string | null;
  };
  rows: RetrievalTraceRow[];
  /** Honours every filter EXCEPT final_status, so the buckets you are not
   * looking at still show their size. */
  facets: {
    final_status: Record<string, number>;
    deciding_stage: Record<string, number>;
  };
  enrichment_notes: Record<string, string> | null;
}

export interface RetrievalChunkRollupCounts {
  candidates: number;
  accepted: number;
  in_prompt: number;
  rejected_applicability: number;
  dropped_by_cap: number;
  top_fused_score: number | null;
}

export interface RetrievalChunkRollup extends RetrievalChunkRollupCounts {
  /** null = the flat active-rule fallback set, which belongs to no chunk. */
  chunk_id: string | null;
  by_corpus: Record<string, RetrievalChunkRollupCounts>;
}

export interface RetrievalChunksStory extends RetrievalRunHead {
  status: "ok";
  chunks: RetrievalChunkRollup[];
  chunks_total: number;
  candidates_total: number;
}

export type RetrievalCandidatesInspection = RetrievalCandidatesPage | RetrievalNoData;
export type RetrievalChunksInspection = RetrievalChunksStory | RetrievalNoData;

/* ---------- admin: retrieval health ----------------------------------------
 * backend/app/api/routes/admin_retrieval_health.py
 *
 * The inspector answers "what happened in THIS run". These answer "is retrieval
 * working at all", across every traced run in a window. Different question,
 * different failure modes: every individual run can look sane while a whole
 * corpus is never retrieved from.
 */

/** Every knob that can silently decide a retrieval outcome. Returned on each
 * health response so a number is never read against the wrong floor. */
export interface RetrievalFloors {
  rag_min_cosine: number;
  rag_min_ts_rank: number;
  rag_score_threshold: number;
  rag_top_k_analysis: number;
  rag_recall_pool: number;
  rag_rrf_k: number;
  pgvector_top_k: number;
}

/** The cross-run analogue of RetrievalNoData: no run_id, because the absence is
 * about the window rather than about one run. */
export interface RetrievalNoHealthData {
  status: "no_retrieval_data";
  reason: string;
  detail?: string;
  window_days: number;
  floors?: RetrievalFloors;
  corpus?: string;
}

/** One corpus's journey from retrieved to cited.
 *
 * `cited` is null when the join that would establish it is unavailable. That is
 * NOT zero and must not render as zero — "we cannot tell" and "never cited"
 * lead to opposite actions. */
export interface RetrievalFunnelRow {
  corpus: string;
  runs: number;
  candidates: number;
  accepted: number;
  rejected_applicability: number;
  in_prompt: number;
  dropped_by_cap: number;
  lost_at_fusion: number;
  dropped_by_retriever: number;
  /** Unreachable while rag_score_threshold is 0.0 — RRF scores are strictly
   * positive. An always-zero column here is expected, not evidence. */
  below_threshold: number;
  distinct_docs: number;
  avg_fused_score: number | null;
  in_prompt_rate: number | null;
  cited: number | null;
  cited_rate: number | null;
}

/** Chunks that drew candidates but got none of them into a prompt — i.e. were
 * graded on nothing retrieved. The most actionable number on the page. */
export interface RetrievalStarvedRow {
  corpus: string;
  starved_chunks: number;
}

export interface RetrievalRejectionReason {
  corpus: string;
  reason: string;
  n: number;
}

/** Floor pressure per (corpus, index). Split by index because one corpus name
 * can cover two tables, and the fallback index is exactly where retrieval
 * behaves differently.
 *
 * `weakest_surviving_cosine` sitting on `min_cosine_floor` means the floor is
 * the binding constraint; sitting well above it means recall is limited by
 * something else. That comparison is the whole point of the row. */
export interface RetrievalFloorPressureRow {
  corpus: string;
  index_name: string;
  queries: number;
  pool_truncated: number;
  returned_nothing: number;
  scope_not_pushed: number;
  weakest_surviving_cosine: number | null;
  mean_weakest_cosine: number | null;
  best_cosine: number | null;
  min_cosine_floor: number | null;
}

export interface RetrievalHealthStory {
  status: "ok";
  window_days: number;
  floors: RetrievalFloors;
  funnel: RetrievalFunnelRow[];
  starved_chunks: RetrievalStarvedRow[];
  top_rejection_reasons: RetrievalRejectionReason[];
  /** Empty when migration 0039 has not been applied yet. That is ABSENT
   * telemetry, not "no floor pressure" — the page must say which. */
  floor_pressure: RetrievalFloorPressureRow[];
  notes: string[];
}

export type RetrievalHealth = RetrievalHealthStory | RetrievalNoHealthData;

/* --- dead corpus: content retrieval never reaches -------------------------- */

export type DeadCorpusSource = "rules" | "precedents" | "precedents_legacy";

/** Identity plus whatever label columns that corpus carries (title, category,
 * severity). Loose on purpose — the projection differs per table. */
export interface DeadCorpusEntry {
  id: string;
  [column: string]: unknown;
}

export interface DeadCorpusUncitedEntry {
  id: string;
  times_in_prompt: number;
}

export interface DeadCorpusStory {
  status: "ok";
  window_days: number;
  corpus: DeadCorpusSource;
  table: string;
  /** Never surfaced as a candidate at all. */
  never_retrieved: DeadCorpusEntry[];
  never_retrieved_shown: number;
  /** Reached a prompt repeatedly and was never cited in a finding. Retrieval
   * works; the content is not earning its place. */
  retrieved_never_cited: DeadCorpusUncitedEntry[];
  retrieved_never_cited_shown: number;
  /** Both lists are capped at this. A short list is not proof of a short tail,
   * so the cap is rendered rather than hidden. */
  limit: number;
}

export type DeadCorpusInspection = DeadCorpusStory | RetrievalNoHealthData;

/* --- per-run query telemetry (migration 0039) ------------------------------ */

/** One hybrid_search call. The floor columns sit next to the weakest survivor
 * so the two are always read together. */
export interface RetrievalQueryRow {
  chunk_id: string | null;
  corpus: string;
  index_name: string;
  category: string | null;
  top_k: number | null;
  recall_pool: number | null;
  vector_returned: number | null;
  keyword_returned: number | null;
  fused_total: number | null;
  /** False when the query text was blank, so the lexical leg never ran. Its
   * zero is a skip, not a miss. */
  keyword_leg_ran: boolean | null;
  min_cosine_seen: number | null;
  max_cosine_seen: number | null;
  min_ts_rank_seen: number | null;
  min_cosine_floor: number | null;
  min_ts_rank_floor: number | null;
  /** The recall pool came back short. Could be the cosine floor, the filters,
   * or a corpus smaller than the pool — this flag does not say which. */
  pool_truncated: boolean | null;
  notes: Record<string, unknown> | null;
}

export interface RetrievalQueryCorpusSummary {
  corpus: string;
  indexes: string[];
  queries: number;
  pool_truncated: number;
  returned_nothing: number;
  keyword_leg_skipped: number;
  scope_not_pushed: number;
  weakest_surviving_cosine: number | null;
  median_weakest_cosine: number | null;
}

export interface RetrievalQueriesStory extends RetrievalRunHead {
  status: "ok";
  floors: RetrievalFloors;
  queries: RetrievalQueryRow[];
  queries_total: number;
  by_corpus: RetrievalQueryCorpusSummary[];
}

export type RetrievalQueriesInspection = RetrievalQueriesStory | RetrievalNoData;

/* --- judged evaluation (RAGAS) --------------------------------------------- */

/** Answers "can this environment evaluate" without spending anything. RAGAS
 * moves its metric surface between minor versions; finding that out mid-run
 * costs money and leaves half-written rows. */
export interface RetrievalEvalCapability {
  installed: boolean;
  version: string | null;
  metrics_available: string[];
  metrics_missing: string[];
  error: string | null;
  supported_metrics: string[];
  default_metrics: string[];
  default_max_samples: number;
  hard_max_samples: number;
  excluded_metrics_note: string;
}

export interface RetrievalEvalSummaryRow {
  corpus: string;
  metric: string;
  samples: number;
  /** Scored and unscored are counted apart on purpose. Folding a NULL into the
   * mean as zero makes "the judge could not decide" and "the context was
   * irrelevant" the same number. */
  scored: number;
  unscored: number;
  mean: number | null;
  min: number | null;
  max: number | null;
}

export interface RetrievalEvalRow {
  corpus: string;
  chunk_id: string | null;
  metric: string;
  score: number | null;
  reason: string | null;
  evaluator: string;
  model: string | null;
  contexts: number | null;
  created_at: string;
}

export interface RetrievalEvaluationStory extends RetrievalRunHead {
  status: "ok";
  summary: RetrievalEvalSummaryRow[];
  rows: RetrievalEvalRow[];
  rows_total: number;
}

/** A run nobody has paid to evaluate yet. Distinct from a run that scored
 * nothing. */
export interface RetrievalNotEvaluated extends RetrievalRunHead {
  status: "not_evaluated";
  hint: string;
}

export type RetrievalEvaluationInspection =
  | RetrievalEvaluationStory
  | RetrievalNotEvaluated;

/** Result of a POST that actually spent tokens. */
export interface RetrievalEvaluateResult {
  status: "ok";
  metrics: string[];
  samples_scored: number;
  /** Samples with no retrieved context. Skipped rather than scored 0 — an
   * absent score, not a bad one. */
  samples_skipped: number;
  rows_written: number;
  summary: RetrievalEvalSummaryRow[];
}


// --- Review assignments and the action trail -------------------------------
// Mirrors backend/app/api/routes/assignments.py `_dict()` field for field.

export type AssignmentStatus =
  | "open" | "in_review" | "awaiting_signoff"
  | "closed" | "superseded" | "cancelled";

export type AssignmentPriority = "low" | "normal" | "high" | "urgent";

/** Mirrors VALID_OUTCOMES in backend/app/services/assignment_service.py. */
export type AssignmentOutcome = "approved" | "rejected" | "cancelled" | "superseded";

export interface ReviewAssignment {
  id: string;
  submission_id: string;
  assignee_id: string;
  assigned_by: string | null;
  status: AssignmentStatus;
  priority: AssignmentPriority;
  due_at: string | null;
  note: string | null;
  outcome: string | null;
  outcome_note: string | null;
  assigned_at: string | null;
  started_at: string | null;
  completed_at: string | null;
  closed_at: string | null;
  superseded_by: string | null;
}

export interface WorkloadRow {
  user_id: string;
  username: string;
  role: string;
  is_active: boolean;
  open_count: number;
}

export interface TrailDiff {
  revision_number: number;
  before: string;
  after: string;
}

export interface TrailRow {
  id: string;
  at: string | null;
  event_type: string;
  actor_id: string | null;
  actor_role: string | null;
  target_type: string | null;
  target_id: string | null;
  metadata: Record<string, unknown>;
  diff: TrailDiff | null;
}

export interface ReviewerTrail {
  activity: TrailRow[];
  stats: { open: number; closed: number; total: number };
}
