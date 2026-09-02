"use client";
import * as React from "react";
import { applySubmissionRevision, listSubmissionRevisions, listSubmissionRuns } from "@/lib/api";
import type { SerializedEditorState } from "lexical";
import type { EditorFixApply, EditorFixMode } from "@/components/editor/EditorApplyPlugin";

/** The three views of the working document, always written together. */
export type LexicalDoc = { state: SerializedEditorState; html: string; text: string };
import type { RevisionSource, RunSummary, ScoreBreakdown, Submission, Violation } from "@/lib/types";

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
  scores: ScoreBreakdown | null;
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
  // True once the document is edited after the newest analysis: the findings on
  // screen describe a superseded version. Server-derived on load, then set
  // locally the moment a revision is saved so the reviewer is told immediately
  // rather than after a refresh. The export block itself is enforced backend-side.
  findingsStale: boolean;
  // Latest rich-editor content, both views together. Null until the reviewer
  // edits in the Lexical editor; sent with the next save. State and HTML are
  // never set apart — export renders the HTML, the editor reloads the state,
  // and a mismatch would show the reviewer one document and export another.
  lexicalDoc: LexicalDoc | null;
  setLexicalDoc: (d: LexicalDoc) => void;
  /** Adopt a rich-editor snapshot that is already persisted (a restore) as
   * both the live and the saved document — no POST. */
  adoptServerLexical: (d: LexicalDoc) => void;
  /** Editor content differs from the last persisted revision. */
  lexicalDirty: boolean;
  /** Persist the editor's current content as a revision. Autosave calls this
   * on idle; the toolbar's Save calls it directly. */
  saveLexical: () => Promise<boolean>;
  /** Called by the mounted rich editor to offer (and on unmount to withdraw)
   * its own apply-fix path. */
  registerEditorApply: (apply: EditorFixApply | null) => void;
  /** Apply a fix THROUGH the rich editor and persist the result as one
   * apply_fix revision.
   *
   * The alternative — splicing the plain-text copy and posting it next to the
   * editor's untouched state and HTML — saved a document that disagreed with
   * itself: the export renders the HTML, so an approved DOCX went out with the
   * original wording and `fix_applied` set against it. Every writer of a fix
   * comes through here so that cannot happen from any of them.
   *
   * "no-editor" means there is no editor mounted to route through; the caller
   * decides whether that is a plain-text submission (splice is fine) or a rich
   * one being viewed in a mode that has no editor on screen (it is not). */
  applyFixInEditor: (
    violation: Violation,
    replacement: string,
    mode: EditorFixMode
  ) => Promise<"applied" | "unlocated" | "already-present" | "save-failed" | "no-editor">;
}

const Context = React.createContext<Ctx | null>(null);

interface ProviderProps {
  submission: Submission;
  initialViolations: Violation[];
  initialScore?: number | null;
  initialGrade?: string | null;
  initialScores?: ScoreBreakdown | null;
  analysisStatus?: string | null;
  analysisMessage?: string | null;
  initialFindingsStale?: boolean;
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
  initialFindingsStale = false,
  children,
}: ProviderProps) {
  const [findingsStale, setFindingsStale] = React.useState(initialFindingsStale);
  const [lexicalDoc, setLexicalDocState] = React.useState<LexicalDoc | null>(null);
  // What was last persisted, so "dirty" means "differs from the server" rather
  // than "the editor emitted something". Lexical fires onChange on load too.
  const savedLexicalRef = React.useRef<string | null>(null);
  const [lexicalDirty, setLexicalDirty] = React.useState(false);
  const lexicalDocRef = React.useRef<LexicalDoc | null>(null);

  const setLexicalDoc = React.useCallback((d: LexicalDoc) => {
    // The editor (LexicalDocument) is the one that knows whether a given
    // emission is trustworthy — it withholds onChange entirely until this
    // mount has settled on its real content (initial state or the import),
    // so by the time an emission reaches here an empty `d.text` is always a
    // genuine, deliberate clear, never the placeholder a fresh mount starts
    // from. See `seededRef` in LexicalDocument.tsx.
    lexicalDocRef.current = d;
    setLexicalDocState(d);
    setLexicalDirty(savedLexicalRef.current !== null && savedLexicalRef.current !== d.html);
    // First emission after load is the seeded document, not an edit.
    if (savedLexicalRef.current === null) savedLexicalRef.current = d.html;
  }, []);

  /** Adopt a revision's rich-editor snapshot as both the live and the saved
   * document, without posting anything — the caller (restoring a prior
   * revision) already persisted it. Mirrors `adoptServerText` for the
   * plain-text side; without this, a restore on the rich-editor path had no
   * way to tell the mode bar its own write was now the saved baseline, so the
   * next autosave would immediately re-post the exact content it just wrote. */
  const adoptServerLexical = React.useCallback((d: LexicalDoc) => {
    lexicalDocRef.current = d;
    setLexicalDocState(d);
    savedLexicalRef.current = d.html;
    setLexicalDirty(false);
  }, []);
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
  // Seeded from current_content when present, else the original upload. Both
  // come from GET /submissions/{id} (see
  // backend/app/api/routes/submissions.py::get_submission), so the working copy
  // is already on screen at first paint; the effect below re-checks it against
  // the newest revision, which is the same record of it.
  const seed = submission.current_content ?? submission.original_content ?? "";
  const [documentText, setDocumentText] = React.useState(seed);
  const [savedText, setSavedText] = React.useState(seed);
  const [unsavedEdits, setUnsavedEdits] = React.useState(0);
  const [saveState, setSaveState] = React.useState<SaveState>("idle");
  // Latest text without waiting for a re-render — every writer goes through
  // applyEdit/adoptServerText, so this ref is the authoritative current value.
  const textRef = React.useRef(seed);
  const seedRef = React.useRef(seed);

  // The newest revision is the same record of current_content, and this page's
  // submission prop can be a cached render — adopt it so a reviewer's earlier
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
          // Both or neither — the backend mirrors them onto the submission as
          // a pair, and only when state is present.
          // Read from the ref, not from state: a fix applied through the editor
          // persists in the same tick as the edit, and the re-render carrying
          // the new `lexicalDoc` has not happened yet. Posting the state from
          // that stale render is exactly how content and HTML came apart.
          lexical_state: lexicalDocRef.current?.state,
          lexical_html: lexicalDocRef.current?.html,
        });
        // A persisted revision is exactly what makes the backend call the
        // findings stale, so reflect it now instead of after a refresh.
        setFindingsStale(true);
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

  // --- Apply-fix through the rich editor ------------------------------------
  const editorApplyRef = React.useRef<EditorFixApply | null>(null);
  const registerEditorApply = React.useCallback((apply: EditorFixApply | null) => {
    editorApplyRef.current = apply;
  }, []);

  const applyFixInEditor = React.useCallback<Ctx["applyFixInEditor"]>(
    async (violation, replacement, mode) => {
      const apply = editorApplyRef.current;
      if (!apply) return "no-editor";
      const result = await apply(violation, replacement, mode);
      if (!result.ok) return result.reason;
      // The editor committed its update synchronously, so `lexicalDocRef` now
      // holds the state, HTML and text of the edited document — and this POST
      // carries all three from it.
      const ok = await applyEdit(result.text, "apply_fix", [violation.id]);
      if (!ok) return "save-failed";
      // That revision IS the editor's content, so the editor is no longer
      // ahead of the server and the idle autosave must not write it again.
      savedLexicalRef.current = lexicalDocRef.current?.html ?? savedLexicalRef.current;
      setLexicalDirty(false);
      return "applied";
    },
    [applyEdit]
  );

  /** Persist the editor's content as a revision.
   *
   * Without this the rich editor had no save at all: onChange only wrote to
   * context, and Save/Discard live in the legacy text pane, which a DOCX or PDF
   * submission never renders. A reviewer's edits reached the server only as a
   * side effect of Apply fix, and were otherwise lost on navigation.
   */
  const saveLexical = React.useCallback(async () => {
    const doc = lexicalDocRef.current;
    if (!doc) return false;
    // Nothing to persist if the editor's content already matches what the
    // server has (including a genuine empty document — see setLexicalDoc).
    if (savedLexicalRef.current === doc.html) return true;
    const ok = await persist(doc.text, "manual_edit");
    if (ok) {
      savedLexicalRef.current = doc.html;
      setLexicalDirty(false);
    }
    return ok;
  }, [persist]);

  // Autosave on idle. The design specifies it ("Unsaved keystrokes — autosave
  // in 2s"), and it is the difference between an editor and a scratchpad.
  // Debounced rather than per-keystroke because every revision marks the run's
  // findings stale, so one revision per burst of typing is the correct grain.
  React.useEffect(() => {
    if (!lexicalDirty) return;
    const t = setTimeout(() => { void saveLexical(); }, 2000);
    return () => clearTimeout(t);
  }, [lexicalDirty, lexicalDoc, saveLexical]);

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
      findingsStale,
      lexicalDoc,
      setLexicalDoc,
      adoptServerLexical,
      lexicalDirty,
      saveLexical,
      registerEditorApply,
      applyFixInEditor,
    }),
    [
      registerEditorApply,
      applyFixInEditor,
      findingsStale,
      lexicalDoc,
      setLexicalDoc,
      adoptServerLexical,
      lexicalDirty,
      saveLexical,
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
