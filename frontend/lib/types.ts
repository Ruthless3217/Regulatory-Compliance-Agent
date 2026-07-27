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

// Semantic document type (Workstream A) — gates product-only obligations (UIN).
export type DocumentType =
  | "product_marketing"
  | "blog_article"
  | "social"
  | "email"
  | "website"
  | "other";

export interface Submission {
  id: string;
  title: string;
  content_type: string;
  document_type?: DocumentType | string | null;
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
  // Workstream C (2026-07-15) — overlap grouping. Findings that quote the same or
  // overlapping span are merged into one group: all members share group_id and
  // exactly one is_primary (the strongest, and the only one scored). The UI shows
  // one expandable card per group and one <mark> per group's primary. NULL
  // group_id = a standalone finding (e.g. document-level disclosure).
  group_id?: string | null;
  is_primary?: boolean | null;
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
