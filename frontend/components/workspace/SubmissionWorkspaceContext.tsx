"use client";
import * as React from "react";
import { applySubmissionRevision, listSubmissionRevisions, listSubmissionRuns } from "@/lib/api";
import type { RevisionSource, RunSummary, Submission, Violation } from "@/lib/types";

export type SaveState = "idle" | "saving" | "error";

interface Ctx {
  submission: Submission;
  // --- Live document text ----------------------------------------------------
  // THE single source of truth for the editable working copy. Both the inline
  // span editor (DocumentPane) and Apply-fix (ViolationCard) read and write
  // through it, so sequential edits compose instead of clobbering each other.
  // `submission.original_content` is the immutable evidentiary copy the
  // violations were graded against and is never written.
  documentText: string;
  /** Local text differs from the last server-confirmed revision. */
  documentDirty: boolean;
  /** Edits applied locally but not yet confirmed by the server. */
  unsavedEdits: number;
  saveState: SaveState;
  /** Apply an edit to the working copy AND persist it as a revision.
   * Resolves false when the POST failed — the edit stays local and dirty. */
  applyEdit: (
    next: string,
    source: RevisionSource,
    appliedViolationIds?: string[]
  ) => Promise<boolean>;
  /** Retry persisting the current working copy (toolbar Save). */
  saveNow: () => Promise<boolean>;
  /** Throw away unsaved local edits, back to the last confirmed revision. */
  revertToSaved: () => void;
  /** Adopt server-side content as both live and saved (restore / initial load). */
  adoptServerText: (content: string) => void;
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

  // --- Live document text ---------------------------------------------------
  // Seeded from current_content when present, else the original upload. NOTE:
  // GET /submissions/{id} does NOT serialize current_content (see
  // backend/app/api/routes/submissions.py::get_submission), so in practice the
  // seed is original_content and the real working copy is adopted from the
  // newest revision by the effect below.
  const seed = submission.current_content ?? submission.original_content ?? "";
  const [documentText, setDocumentText] = React.useState(seed);
  const [savedText, setSavedText] = React.useState(seed);
  const [unsavedEdits, setUnsavedEdits] = React.useState(0);
  const [saveState, setSaveState] = React.useState<SaveState>("idle");
  // Latest text without waiting for a re-render — every writer goes through
  // applyEdit/adoptServerText, so this ref is the authoritative current value.
  const textRef = React.useRef(seed);
  const seedRef = React.useRef(seed);

  // The submission endpoint drops current_content, but the revisions list is
  // the same record of it — adopt the newest revision so a reviewer's earlier
  // edits survive a reload instead of the pane snapping back to the original.
  React.useEffect(() => {
    let cancelled = false;
    listSubmissionRevisions(submission.id)
      .then((res) => {
        if (cancelled || res.revisions.length === 0) return;
        const latest = res.revisions.reduce((a, r) =>
          r.revision_number > a.revision_number ? r : a
        );
        // Never stomp edits the reviewer has already made in this session.
        if (textRef.current !== seedRef.current) return;
        textRef.current = latest.content;
        setDocumentText(latest.content);
        setSavedText(latest.content);
      })
      .catch(() => {
        /* non-fatal: the pane just shows the original upload */
      });
    return () => {
      cancelled = true;
    };
  }, [submission.id]);

  // ponytail: last-write-wins on concurrent saves — two POSTs in flight at once
  // both land as revisions and the later response sets savedText. Fine because
  // every UI writer awaits its own call and edits compose off `textRef`.
  // Upgrade path: a single-slot save queue if background autosave ever lands.
  const persist = React.useCallback(
    async (
      content: string,
      source: RevisionSource,
      appliedViolationIds?: string[]
    ): Promise<boolean> => {
      setSaveState("saving");
      try {
        await applySubmissionRevision(submission.id, {
          content,
          source,
          applied_violation_ids: appliedViolationIds?.length ? appliedViolationIds : undefined,
        });
        // Only the save carrying the newest text may declare the doc clean —
        // an older POST resolving late must not mark newer edits as saved.
        if (textRef.current === content) {
          setSavedText(content);
          setUnsavedEdits(0);
          setSaveState("idle");
        } else {
          setUnsavedEdits((n) => Math.max(0, n - 1));
        }
        return true;
      } catch {
        setSaveState("error");
        return false;
      }
    },
    [submission.id]
  );

  const applyEdit = React.useCallback(
    (next: string, source: RevisionSource, appliedViolationIds?: string[]) => {
      textRef.current = next;
      setDocumentText(next);
      setUnsavedEdits((n) => n + 1);
      return persist(next, source, appliedViolationIds);
    },
    [persist]
  );

  const saveNow = React.useCallback(
    () => persist(textRef.current, "manual_edit"),
    [persist]
  );

  const adoptServerText = React.useCallback((content: string) => {
    textRef.current = content;
    setDocumentText(content);
    setSavedText(content);
    setUnsavedEdits(0);
    setSaveState("idle");
  }, []);

  const revertToSaved = React.useCallback(
    () => adoptServerText(savedText),
    [adoptServerText, savedText]
  );

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
      documentText,
      documentDirty: documentText !== savedText,
      unsavedEdits,
      saveState,
      applyEdit,
      saveNow,
      revertToSaved,
      adoptServerText,
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
      documentText,
      savedText,
      unsavedEdits,
      saveState,
      applyEdit,
      saveNow,
      revertToSaved,
      adoptServerText,
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
