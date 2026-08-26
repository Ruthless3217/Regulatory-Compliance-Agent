"use client";
import * as React from "react";
import {
  applySubmissionRevision,
  listSubmissionRevisions,
  listSubmissionRuns,
  revisionConflict,
} from "@/lib/api";
import type { SerializedEditorState } from "lexical";
import type { EditorFixApply, EditorFixMode } from "@/components/editor/EditorApplyPlugin";

/** The three views of the working document, always written together. */
export type LexicalDoc = { state: SerializedEditorState; html: string; text: string };
import type { RevisionSource, RunSummary, ScoreBreakdown, Submission, Violation } from "@/lib/types";

export type SaveState = "idle" | "saving" | "error" | "conflict";

/** Someone else saved this document while the reviewer was editing it.
 *
 * The server refused the write, so the reviewer's work exists only in their
 * browser. Nothing here reloads, merges or discards on its own — the whole
 * point is that the next move is the reviewer's, made knowingly. */
/** Whether the revision this editor is saving against has been established.
 *
 * "loading"     — the fetch is in flight; saving must wait rather than write
 *                 without a base and forfeit the check.
 * "ready"       — a base revision is known; saves are protected.
 * "unavailable" — the fetch failed. Saving stays blocked and the reviewer is
 *                 told, because the alternative is writing unchecked and
 *                 silently losing the protection for the whole session. */
export type BaselineState = "loading" | "ready" | "unavailable";

/** One canonical write, described completely enough to be retried as the same
 * operation. A conflicted apply-fix must come back as an apply-fix carrying
 * its violation ids, or the fix lands without ever being recorded as applied. */
export interface SaveIntent {
  content: string;
  source: RevisionSource;
  appliedViolationIds?: string[];
  note?: string;
  /** Restore posts plain text only, exactly as it did before it moved onto
   * this path: the mounted editor still holds the pre-restore document, and
   * shipping that as this revision's working copy would leave content and
   * working document describing different text. */
  omitWorkingDocument?: boolean;
}

export interface RevisionConflictState {
  /** The revision this reviewer's edit was built on. */
  expectedRevision: number | null;
  /** The revision the document is actually at now. */
  currentRevision: number;
  /** Always false — the server wrote nothing. Kept explicit because it is the
   * one fact the whole conflict UI turns on. */
  saved: false;
}

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
  /** Set when a save was refused because the document moved on. While this is
   * set, autosave is halted and every save path is a no-op: retrying the same
   * stale payload would only be refused again, forever. */
  conflict: RevisionConflictState | null;
  /** Whether a base revision is known. Saving is blocked unless this is
   * "ready" — an unchecked write is exactly the lost update this all exists
   * to prevent. */
  baseline: BaselineState;
  /** Re-attempt the base-revision fetch after a failure. */
  retryBaseline: () => void;
  /** Restore an old revision as a new one, through the same protected write
   * path as every other mutation. Resolves false when refused. */
  restoreRevision: (rev: { content: string; revision_number: number }) => Promise<boolean>;
  /** Deliberately supersede the newer revision with the local one, at the
   * reviewer's explicit request. Re-bases onto the server's current revision
   * and saves; their revision stays in the history, it is not erased. Refused
   * again (a fresh conflict) if the document moved on once more. */
  keepMyVersion: () => Promise<boolean>;
  /** Throw away unsaved local edits, back to the last confirmed revision. */
  revertToSaved: () => void;
  /** Adopt server-side content as both live and saved (restore / initial
   * load). Pass the revision it came from so the next save is based on it —
   * without that the following autosave conflicts with the reviewer's own
   * adoption. */
  adoptServerText: (content: string, revisionNumber?: number) => void;
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

  /** Record that `doc` is now the persisted version of the editor.
   *
   * Dirty is recomputed against what the editor holds RIGHT NOW, not against
   * the document captured before the request went out. A reviewer typing
   * during a save would otherwise have that text marked as saved and silently
   * skipped by the next autosave — which is exactly how a coalesced save gets
   * lost instead of deferred. */
  const markLexicalSaved = React.useCallback((doc: LexicalDoc) => {
    savedLexicalRef.current = doc.html;
    setLexicalDirty((lexicalDocRef.current?.html ?? doc.html) !== doc.html);
  }, []);

  const setLexicalDoc = React.useCallback((d: LexicalDoc) => {
    // A freshly-mounted editor emits an EMPTY document before its content
    // arrives, and this provider outlives the editor — switching View/Split/Edit
    // re-parents it, so React remounts it and that empty emission lands here
    // with `savedLexicalRef` already holding the real document. Treating it as
    // an edit marks the submission dirty and lets the next autosave write a
    // blank revision over the reviewer's working copy.
    //
    // So an empty document is only ever accepted as the truth when what we hold
    // is also empty. Clearing a document deliberately still works — it goes
    // through the editor with content already loaded, so `text` is empty only
    // after the reviewer has actually emptied it, which the seeded ref reflects.
    if (!d.text.trim() && (savedLexicalRef.current ?? "").trim()) {
      return;
    }
    lexicalDocRef.current = d;
    setLexicalDocState(d);
    setLexicalDirty(savedLexicalRef.current !== null && savedLexicalRef.current !== d.html);
    // First emission after load is the seeded document, not an edit.
    if (savedLexicalRef.current === null) savedLexicalRef.current = d.html;
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
  // The revision every save claims as its base. null means "not established",
  // and while it is null NOTHING is written: the server would treat a save
  // without it as an unchecked legacy write, so a failed or still-pending
  // fetch would silently downgrade the whole session to last-write-wins.
  const expectedRevisionRef = React.useRef<number | null>(null);
  const [baseline, setBaseline] = React.useState<BaselineState>("loading");
  // The last write we attempted, kept so a conflict can be retried as the same
  // operation rather than as a generic text save.
  const lastIntentRef = React.useRef<SaveIntent | null>(null);
  // Single-flight. At most one canonical write may be open for this editor;
  // a save asked for while one is open is remembered, not dropped.
  const inFlightRef = React.useRef(false);
  const pendingSaveRef = React.useRef(false);
  const [conflict, setConflict] = React.useState<RevisionConflictState | null>(null);
  // persist() closes over the state value from the render that created it;
  // the ref is what makes "stop retrying" actually stop.
  const conflictRef = React.useRef<RevisionConflictState | null>(null);

  // The newest revision is the same record of current_content, and this page's
  // submission prop can be a cached render — adopt it so a reviewer's earlier
  // edits survive a reload instead of the pane snapping back to the original.
  const baselineRunRef = React.useRef(0);

  const loadBaseline = React.useCallback(() => {
    const run = ++baselineRunRef.current;
    setBaseline("loading");
    listSubmissionRevisions(submission.id)
      .then((res) => {
        if (run !== baselineRunRef.current) return;
        // The head number is adopted even when the text is not: it is what
        // this reviewer is editing against either way.
        const latest = res.revisions.length
          ? res.revisions.reduce((a, r) => (r.revision_number > a.revision_number ? r : a))
          : null;
        // 0 is a real base, not a null: "a document nobody had edited".
        expectedRevisionRef.current = latest ? latest.revision_number : 0;
        setBaseline("ready");
        if (!latest) return;
        // Never stomp edits the reviewer has already made in this session.
        if (textRef.current !== seedRef.current) return;
        textRef.current = latest.content;
        setDocumentText(latest.content);
        setSavedText(latest.content);
      })
      .catch(() => {
        if (run !== baselineRunRef.current) return;
        // Deliberately NOT swallowed. Before optimistic locking this was
        // cosmetic — the pane just showed the original upload. Now it decides
        // whether saves are checked at all, so it has to be visible and
        // retryable instead of quietly turning the protection off.
        setBaseline("unavailable");
      });
  }, [submission.id]);

  React.useEffect(() => {
    loadBaseline();
    return () => {
      // Abandon the in-flight result rather than let a stale one land.
      baselineRunRef.current += 1;
    };
  }, [loadBaseline]);

  // Concurrent saves are decided by the server now, not by whichever POST
  // resolves last: each one states the revision it was built on, and a save
  // built on a stale view comes back 409 with nothing written.
  const persist = React.useCallback(
    async (
      intent: SaveIntent,
      // Only the reviewer's explicit "keep my version" sets this. Everything
      // else — autosave, Apply fix, the toolbar, restore — stays blocked while
      // a conflict stands, because the payload is stale by construction.
      overrideConflict = false
    ): Promise<boolean> => {
      if (conflictRef.current && !overrideConflict) return false;
      // No base revision, no write. Posting without `expected_revision` would
      // be accepted by the server (legacy callers still need that) and would
      // silently give up the protection for this document.
      if (expectedRevisionRef.current === null) {
        pendingSaveRef.current = true;
        return false;
      }
      // At most one canonical write open at a time. A save asked for while one
      // is in flight is remembered — the autosave effect re-runs when this one
      // settles and writes whatever the document says then, so nothing is lost
      // and there is never a second POST racing the first.
      if (inFlightRef.current) {
        pendingSaveRef.current = true;
        return false;
      }
      inFlightRef.current = true;
      lastIntentRef.current = intent;
      setSaveState("saving");
      try {
        const saved = await applySubmissionRevision(submission.id, {
          expected_revision: expectedRevisionRef.current,
          content: intent.content,
          source: intent.source,
          note: intent.note,
          applied_violation_ids: intent.appliedViolationIds?.length
            ? intent.appliedViolationIds
            : undefined,
          // Both or neither — the backend mirrors them onto the submission as
          // a pair, and only when state is present.
          // Read from the ref, not from state: a fix applied through the editor
          // persists in the same tick as the edit, and the re-render carrying
          // the new `lexicalDoc` has not happened yet. Posting the state from
          // that stale render is exactly how content and HTML came apart.
          lexical_state: intent.omitWorkingDocument ? undefined : lexicalDocRef.current?.state,
          lexical_html: intent.omitWorkingDocument ? undefined : lexicalDocRef.current?.html,
        });
        // The write that just landed is the base for the next one.
        expectedRevisionRef.current = saved.revision_number;
        lastIntentRef.current = null;
        // A persisted revision is exactly what makes the backend call the
        // findings stale, so reflect it now instead of after a refresh.
        setFindingsStale(true);
        // Only the save carrying the newest text may declare the doc clean —
        // an older POST resolving late must not mark newer edits as saved.
        if (textRef.current === intent.content) {
          setSavedText(intent.content);
          setUnsavedEdits(0);
        } else {
          setUnsavedEdits((n) => Math.max(0, n - 1));
        }
        // Single-flight means nothing else is writing, so the spinner is over
        // regardless of whose text just landed. (Before this it was only
        // cleared on the textRef match, which the editor path never satisfies,
        // so "Saving…" stuck forever and the effect below could not re-fire.)
        setSaveState("idle");
        return true;
      } catch (e) {
        const detail = revisionConflict(e);
        if (detail) {
          // Nothing was written. The reviewer's text stays exactly where it
          // is — in the editor, dirty — and every automatic save path stops
          // until they choose what to do with it.
          const next: RevisionConflictState = {
            expectedRevision: detail.expected_revision,
            currentRevision: detail.current_revision,
            saved: false,
          };
          conflictRef.current = next;
          setConflict(next);
          setSaveState("conflict");
          return false;
        }
        setSaveState("error");
        return false;
      } finally {
        inFlightRef.current = false;
      }
    },
    [submission.id]
  );

  /** Save the local document over the newer server revision, on purpose.
   *
   * Not a merge and not a silent overwrite: the reviewer has been shown the
   * conflict and asked for this. The superseded revision stays in the history,
   * so "keep mine" costs nobody their work — it only moves the head. */
  const keepMyVersion = React.useCallback(async () => {
    const standing = conflictRef.current;
    const refused = lastIntentRef.current;
    if (!standing || !refused) return false;
    // Re-base onto what the server said is current. If it has moved again
    // since, this save is refused in turn and a fresh conflict is raised.
    expectedRevisionRef.current = standing.currentRevision;
    conflictRef.current = null;
    setConflict(null);
    const doc = lexicalDocRef.current;
    // The reviewer's newest text, but the ORIGINAL operation's metadata: a
    // conflicted apply-fix retried as a plain manual_edit would put the fix in
    // the document while leaving the finding recorded as never applied.
    const ok = await persist(
      { ...refused, content: doc?.text ?? textRef.current },
      true
    );
    if (!ok) return false;
    if (doc) markLexicalSaved(doc);
    if (refused.appliedViolationIds?.length) {
      const ids = new Set(refused.appliedViolationIds);
      const appliedAt = new Date().toISOString();
      setViolations((prev) =>
        prev.map((v) =>
          ids.has(v.id) ? { ...v, fix_applied: true, fix_applied_at: appliedAt } : v
        )
      );
    }
    return true;
  }, [persist, markLexicalSaved]);

  /** Restore an old revision by writing it as a new one — the same primitive
   * and the same protection as every other mutation, not a second save path.
   * Local state moves only after the server accepts it, so a refused restore
   * leaves the reviewer's document exactly as it was. */
  const restoreRevision = React.useCallback(
    async (rev: { content: string; revision_number: number }) => {
      const ok = await persist({
        content: rev.content,
        source: "restore",
        note: `Restored from revision ${rev.revision_number}`,
        omitWorkingDocument: true,
      });
      if (!ok) return false;
      textRef.current = rev.content;
      setDocumentText(rev.content);
      setSavedText(rev.content);
      setUnsavedEdits(0);
      return true;
    },
    [persist]
  );

  const applyEdit = React.useCallback(
    (next: string, source: RevisionSource, appliedViolationIds?: string[]) => {
      textRef.current = next;
      setDocumentText(next);
      setUnsavedEdits((n) => n + 1);
      return persist({ content: next, source, appliedViolationIds });
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
    // Second line of the same defence as setLexicalDoc: whatever route got us
    // here — autosave, the toolbar, Apply fix — an empty document never
    // overwrites a non-empty saved one. The cost of being wrong is the
    // reviewer's corrected wording.
    if (!doc.text.trim() && (savedLexicalRef.current ?? "").trim()) return false;
    if (savedLexicalRef.current === doc.html) return true;
    const ok = await persist({ content: doc.text, source: "manual_edit" });
    if (ok) markLexicalSaved(doc);
    return ok;
  }, [persist, markLexicalSaved]);

  // Autosave on idle. The design specifies it ("Unsaved keystrokes — autosave
  // in 2s"), and it is the difference between an editor and a scratchpad.
  // Debounced rather than per-keystroke because every revision marks the run's
  // findings stale, so one revision per burst of typing is the correct grain.
  React.useEffect(() => {
    if (!lexicalDirty) return;
    // A standing conflict halts the timer outright. Re-posting a payload the
    // server has already refused cannot succeed, and one request every two
    // seconds would bury the single message the reviewer needs to read.
    if (conflict) return;
    // No base revision, no autosave. The reviewer keeps typing and the text
    // stays safe in the editor; the banner tells them saving is paused, and a
    // successful retry re-runs this effect and writes it.
    if (baseline !== "ready") return;
    const t = setTimeout(() => { void saveLexical(); }, 2000);
    return () => clearTimeout(t);
    // `saveState` is a dependency so this re-runs when a write settles: that is
    // what picks up a save that was coalesced away while one was in flight,
    // and it writes the document as it stands then rather than the stale text
    // the dropped request was carrying.
  }, [lexicalDirty, lexicalDoc, saveLexical, conflict, baseline, saveState]);

  const saveNow = React.useCallback(
    () => persist({ content: textRef.current, source: "manual_edit" }),
    [persist]
  );

  const adoptServerText = React.useCallback((content: string, revisionNumber?: number) => {
    textRef.current = content;
    setDocumentText(content);
    setSavedText(content);
    setUnsavedEdits(0);
    // Adopting server state ends the conflict — the reviewer is no longer
    // holding anything the server refused — and re-bases onto the revision the
    // text came from. Without the re-base the very next autosave would be
    // refused for being stale against the reviewer's own adoption.
    // Only a caller that knows WHICH revision this text came from can end a
    // conflict: clearing it without re-basing would just walk into the same
    // 409 on the next save. revertToSaved passes nothing and deliberately
    // leaves the conflict standing.
    if (revisionNumber !== undefined) {
      expectedRevisionRef.current = revisionNumber;
      setBaseline("ready");
      conflictRef.current = null;
      lastIntentRef.current = null;
      setConflict(null);
      setSaveState("idle");
    }
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
      conflict,
      baseline,
      retryBaseline: loadBaseline,
      restoreRevision,
      keepMyVersion,
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
      lexicalDirty,
      saveLexical,
      submission,
      documentText,
      savedText,
      unsavedEdits,
      saveState,
      conflict,
      baseline,
      loadBaseline,
      restoreRevision,
      keepMyVersion,
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
