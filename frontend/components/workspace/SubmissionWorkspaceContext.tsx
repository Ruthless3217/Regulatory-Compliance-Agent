"use client";
import * as React from "react";
import { listSubmissionRuns } from "@/lib/api";
import type { RunSummary, Submission, Violation } from "@/lib/types";

interface Ctx {
  submission: Submission;
  violations: Violation[];
  setViolations: (v: Violation[] | ((prev: Violation[]) => Violation[])) => void;
  selectedViolationId: string | null;
  setSelectedViolationId: (id: string | null) => void;
  overallScore: number | null;
  grade: string | null;
  setScore: (score: number | null, grade: string | null) => void;
  scores: Record<string, number> | null;
  // Status + message from the compliance result. `analysisMessage` is set when
  // the run was degraded / needs review — the signal the UI uses to avoid
  // showing an un-gradeable document as "clean".
  analysisStatus: string | null;
  analysisMessage: string | null;
  // True when the document could NOT be cleanly graded (degraded/failed/needs
  // review). Distinct from a genuine clean grade (which has no message).
  analysisIncomplete: boolean;
  // Optimistic "analyzing" flag: SubmissionHeader's rerun button sets this
  // immediately on click, before router.refresh() lands a fresh `submission`
  // prop with the real status — so the progress bar/SSE stream in ReviewTab
  // starts right away instead of waiting a full round trip. Cleared once the
  // real submission.status catches up (or the caller clears it on error).
  optimisticAnalyzing: boolean;
  setOptimisticAnalyzing: (v: boolean) => void;
  // Reviewer-facing run history (GET /compliance/submissions/{id}/runs),
  // oldest to newest — shared so SubmissionHeader's "Run #N of M" picker and
  // ReviewTab's historical-run banner agree on the same selection.
  runs: RunSummary[];
  // null = viewing the latest run (no explicit selection).
  selectedRunId: string | null;
  setSelectedRunId: (id: string | null) => void;
}

const Context = React.createContext<Ctx | null>(null);

interface ProviderProps {
  submission: Submission;
  initialViolations: Violation[];
  initialScore?: number | null;
  initialGrade?: string | null;
  initialScores?: Record<string, number> | null;
  analysisStatus?: string | null;
  analysisMessage?: string | null;
  children: React.ReactNode;
}

const ANALYZING_STATUSES = new Set(["analyzing", "preprocessing", "uploaded"]);

export function SubmissionWorkspaceProvider({
  submission,
  initialViolations,
  initialScore = null,
  initialGrade = null,
  initialScores = null,
  analysisStatus = null,
  analysisMessage = null,
  children,
}: ProviderProps) {
  const [violations, setViolations] = React.useState<Violation[]>(initialViolations);
  const [selectedViolationId, setSelectedViolationId] = React.useState<string | null>(null);
  const [overallScore, setOverallScore] = React.useState<number | null>(initialScore);
  const [grade, setGrade] = React.useState<string | null>(initialGrade);
  const [optimisticAnalyzing, setOptimisticAnalyzing] = React.useState(false);
  const [runs, setRuns] = React.useState<RunSummary[]>([]);
  const [selectedRunId, setSelectedRunId] = React.useState<string | null>(null);

  const setScore = (score: number | null, g: string | null) => {
    setOverallScore(score);
    setGrade(g);
  };

  // Once the server-confirmed status genuinely reflects an in-flight analysis,
  // the optimistic flag has done its job — drop it so it can't get stuck true
  // forever if, e.g., the rerun's background task never flips status back.
  React.useEffect(() => {
    if (optimisticAnalyzing && ANALYZING_STATUSES.has(submission.status)) {
      setOptimisticAnalyzing(false);
    }
  }, [submission.status, optimisticAnalyzing]);

  // Run history has no server-rendered source (unlike violations/score, which
  // layout.tsx fetches up front) — fetch client-side, and refresh whenever the
  // submission identity or status changes so a freshly-created run (rerun,
  // or the initial analysis completing) shows up without a manual reload.
  React.useEffect(() => {
    let cancelled = false;
    listSubmissionRuns(submission.id)
      .then((res) => {
        if (!cancelled) setRuns(res.runs);
      })
      .catch(() => {
        /* non-fatal: the run picker/banner just stay empty */
      });
    return () => {
      cancelled = true;
    };
  }, [submission.id, submission.status]);

  const analysisIncomplete =
    !!analysisMessage ||
    analysisStatus === "failed" ||
    analysisStatus === "waiting_for_review";

  const value = React.useMemo(
    () => ({
      submission,
      violations,
      setViolations,
      selectedViolationId,
      setSelectedViolationId,
      overallScore,
      grade,
      setScore,
      scores: initialScores,
      analysisStatus,
      analysisMessage,
      analysisIncomplete,
      optimisticAnalyzing,
      setOptimisticAnalyzing,
      runs,
      selectedRunId,
      setSelectedRunId,
    }),
    [
      submission,
      violations,
      selectedViolationId,
      overallScore,
      grade,
      initialScores,
      analysisStatus,
      analysisMessage,
      analysisIncomplete,
      optimisticAnalyzing,
      runs,
      selectedRunId,
    ]
  );

  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useSubmissionWorkspace(): Ctx {
  const v = React.useContext(Context);
  if (!v) throw new Error("useSubmissionWorkspace must be used inside SubmissionWorkspaceProvider");
  return v;
}
