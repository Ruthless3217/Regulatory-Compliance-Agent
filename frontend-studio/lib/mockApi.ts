/**
 * Signature-compatible mock client — mirrors `frontend/lib/api.ts` function
 * names/return shapes so screens built against this can be swapped to the
 * real client without touching call sites. Returns typed fixtures from
 * `./mock` behind artificial latency, plus simulated SSE async-generators.
 */
import type {
  AuditRow,
  ComplianceResults,
  DashboardSummary,
  DocUsageRow,
  DocumentComparison,
  RuleAuditRow,
  RunRow,
  SessionRow,
  Submission,
  SSEAnalyzeChunk,
  SSEAnalyzeScore,
  SSEAnalyzeStage,
  TimeseriesResponse,
  TopRulesResponse,
  UsageSummary,
  UserRow,
} from "./types";
import * as M from "./mock";

const delay = <T,>(v: T, ms = 350) => new Promise<T>((r) => setTimeout(() => r(v), ms));

/* ---------- submissions ---------- */
export const listSubmissions = (): Promise<{ submissions: Submission[]; total?: number }> =>
  delay({ submissions: M.submissions, total: M.submissions.length });
export const getSubmission = (id: string): Promise<Submission> =>
  delay(M.submissions.find((s) => s.id === id) ?? M.submissions[0]);
export const classifySubmission = (_content: string): Promise<{ document_type: string }> =>
  delay({ document_type: "product_marketing" }, 500);

/* ---------- compliance ---------- */
export const getComplianceResults = (_id: string): Promise<ComplianceResults> => delay(M.complianceResults);

/* ---------- dashboard ---------- */
export const getDashboardSummary = (): Promise<DashboardSummary> => delay(M.dashboardSummary);
export const getViolationsByCategory = () => delay(M.violationsByCategory);
export const getViolationsBySeverity = () => delay(M.violationsBySeverity);
export const getDashboardTimeseries = (_bucket: "day" | "week" = "day"): Promise<TimeseriesResponse> =>
  delay(M.timeseries);
export const getTopRules = (_limit = 10): Promise<TopRulesResponse> => delay(M.topRules);

/* ---------- comparisons ---------- */
export const listComparisons = (): Promise<{ total: number; comparisons: DocumentComparison[] }> =>
  delay({ total: M.comparisons.length, comparisons: M.comparisons });
export const getComparison = (id: string): Promise<DocumentComparison> =>
  delay(M.comparisons.find((c) => c.id === id) ?? M.comparisons[0]);

/* ---------- super-admin console ---------- */
export const listUsers = (): Promise<UserRow[]> => delay(M.users);
export const usageSummary = (): Promise<UsageSummary> => delay(M.usageSummary);
export const usageByDocument = (): Promise<DocUsageRow[]> => delay(M.usageByDocument);
export const listRuns = (): Promise<RunRow[]> => delay(M.runs);
export const listSessions = (): Promise<SessionRow[]> => delay(M.sessions);
export const auditFeed = (): Promise<AuditRow[]> => delay(M.auditRows);
export const ruleAudit = (): Promise<RuleAuditRow[]> => delay(M.ruleAuditRows);

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms));

/* ---------- simulated SSE ---------- */
export async function* simulateAnalyze(
  _id: string
): AsyncGenerator<SSEAnalyzeStage | SSEAnalyzeChunk | SSEAnalyzeScore | { done: true }> {
  const stages: SSEAnalyzeStage["stage"][] = ["preprocess", "dispatch", "analysis", "scoring"];
  for (let i = 0; i < stages.length; i++) {
    yield { stage: stages[i], progress: Math.round(((i + 1) / stages.length) * 100) };
    await sleep(600);
  }
  for (const chunk of M.streamChunks) {
    yield chunk;
    await sleep(500);
  }
  yield { overall_score: M.complianceResults.overall_score!, grade: M.complianceResults.grade!, scores: M.complianceResults.scores! };
  yield { done: true };
}

export async function* simulateChat(_message: string): AsyncGenerator<{ token: string } | { done: true }> {
  const text = "Based on the cited IRDAI precedent, this claim needs a supporting disclosure. ";
  for (const tok of text.split(" ")) {
    yield { token: tok + " " };
    await sleep(60);
  }
  yield { done: true };
}
