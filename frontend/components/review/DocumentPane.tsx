"use client";
import * as React from "react";
import { toast } from "sonner";
import { Check, Loader2, Sparkles, TriangleAlert } from "lucide-react";
import { buildParagraphs, violationsToHighlightables, type DocPiece } from "@/lib/highlightMarkup";
import { createReviewerViolation, createSubmissionComment } from "@/lib/api";
import { categoryLabel, normalizeSeverity } from "@/lib/format";
import { useSubmissionWorkspace } from "@/components/workspace/SubmissionWorkspaceContext";
import { VersionHistoryPopover } from "./VersionHistoryPopover";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { RevisionSource, Violation } from "@/lib/types";

interface Props {
  violations: Violation[];
  selectedViolationId: string | null;
  onSelect: (id: string) => void;
  /** Historical-run view: the findings on screen are from a past run, so
   * editing the live document from here would be misleading. */
  readOnly?: boolean;
}

/** A live text selection with the floating composer open on it. `mode` picks
 * what the reviewer is writing: a freestanding note, or a brand-new flagged
 * issue over text the model never surfaced. */
interface PendingSelection {
  anchorText: string;
  x: number;
  y: number;
  mode: "comment" | "flag";
}

/** Severity/category choices for a hand-written flag. Severities are the
 * 4-tier scale the UI buckets by (see normalizeSeverity); categories start
 * from the canonical set and pick up anything else already on this document,
 * so a reviewer is never forced into a category the corpus doesn't use. */
const FLAG_SEVERITIES = ["critical", "high", "medium", "low"] as const;
const CANONICAL_CATEGORIES = ["irdai", "sebi", "brand", "regulatory", "seo"];

const FIELD_CLASS =
  "h-8 w-full rounded-sm border border-border bg-background px-2 text-xs " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary";

/** The span currently open for editing, pinned by offset + the exact text it
 * was opened against (so a stale offset can be detected at commit time). */
interface EditTarget {
  violationId: string;
  start: number;
  end: number;
  text: string;
}

/** Multi-line or long spans get a textarea (Enter inserts a newline, explicit
 * Save commits); short single-line spans get an input where Enter commits. */
const isBlockSpan = (s: string) => s.includes("\n") || s.length > 90;

export function DocumentPane({ violations, selectedViolationId, onSelect, readOnly }: Props) {
  const { submission, documentText, applyEdit, setViolations } = useSubmissionWorkspace();
  const paragraphs = React.useMemo(
    () => buildParagraphs(documentText || "", violationsToHighlightables(violations)),
    [documentText, violations]
  );
  const containerRef = React.useRef<HTMLDivElement>(null);

  const [editing, setEditing] = React.useState<EditTarget | null>(null);
  const [pending, setPending] = React.useState<PendingSelection | null>(null);
  const [commentBody, setCommentBody] = React.useState("");
  const [busy, setBusy] = React.useState(false);
  // Set to a freshly-created flag's id so the effect below opens the SAME
  // per-span editor model flags use, as soon as its <mark> is rendered.
  const [autoEditId, setAutoEditId] = React.useState<string | null>(null);
  const [flag, setFlag] = React.useState({
    severity: "medium",
    category: "irdai",
    description: "",
    suggestedFix: "",
  });

  const violationById = React.useMemo(
    () => new Map(violations.map((v) => [v.id, v])),
    [violations]
  );

  const categoryChoices = React.useMemo(
    () => Array.from(new Set([...CANONICAL_CATEGORIES, ...violations.map((v) => v.category)])),
    [violations]
  );

  // Toggle data-selected on the matching <mark>. Imperative on purpose: these
  // attributes are NOT set in JSX, so React never fights this effect over them.
  React.useEffect(() => {
    const root = containerRef.current;
    if (!root) return;
    const marks = root.querySelectorAll<HTMLElement>("mark[data-violation-id]");
    marks.forEach((m) => {
      const isSel = m.dataset.violationId === selectedViolationId;
      m.dataset.selected = isSel ? "true" : "false";
      if (isSel) {
        m.scrollIntoView({ behavior: "smooth", block: "center" });
        m.dataset.pulse = "true";
        setTimeout(() => { if (m) m.dataset.pulse = "false"; }, 850);
      }
    });
  }, [selectedViolationId, paragraphs]);

  const openEditor = (piece: DocPiece) => {
    if (!piece.violationId) return;
    onSelect(piece.violationId);
    if (readOnly) return;
    setEditing({
      violationId: piece.violationId,
      start: piece.start,
      end: piece.end,
      text: piece.text,
    });
  };

  // A just-created reviewer flag has no offsets of its own — buildParagraphs
  // re-locates it from its current_text on the next render, and this picks the
  // resulting piece up so the reviewer lands straight in the span editor.
  React.useEffect(() => {
    if (!autoEditId) return;
    for (const pieces of paragraphs) {
      const piece = pieces.find((p) => p.violationId === autoEditId);
      if (piece) {
        openEditor(piece);
        break;
      }
    }
    setAutoEditId(null);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [autoEditId, paragraphs]);

  /** Splice a span replacement into the live document and persist it. */
  const commitSpan = async (target: EditTarget, replacement: string, source: RevisionSource) => {
    const cur = documentText;
    let next: string;
    if (cur.slice(target.start, target.end) === target.text) {
      next = cur.slice(0, target.start) + replacement + cur.slice(target.end);
    } else if (cur.includes(target.text)) {
      // Offsets went stale (another edit landed while this editor was open) —
      // fall back to replacing the same literal text wherever it now sits.
      // Function form: a replacement containing "$&" must stay literal.
      next = cur.replace(target.text, () => replacement);
    } else {
      toast.error("The document changed under this edit — reopen the section and try again.");
      setEditing(null);
      return;
    }
    setEditing(null);
    const ok = await applyEdit(next, source, [target.violationId]);
    if (!ok) {
      toast.error("Edit applied locally but not saved — use Save in the toolbar to retry.");
      return;
    }
    // The revisions endpoint flips fix_applied for every applied_violation_id;
    // mirror it so the sidebar card agrees without a reload.
    const appliedAt = new Date().toISOString();
    setViolations((prev) =>
      prev.map((v) =>
        v.id === target.violationId ? { ...v, fix_applied: true, fix_applied_at: appliedAt } : v
      )
    );
    toast.success(source === "apply_fix" ? "Suggested fix applied" : "Section updated");
  };

  // Any selected text — flagged or not — can become a freestanding note OR a
  // new flagged issue. Capture it and float a small composer near it.
  const cancelPending = React.useCallback(() => {
    setPending(null);
    setCommentBody("");
    setFlag({ severity: "medium", category: "irdai", description: "", suggestedFix: "" });
  }, []);

  const handleMouseUp = () => {
    if (editing) return; // selecting inside the span editor is not an anchor
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return;
    const anchorText = sel.toString().trim();
    if (!anchorText) return;
    const root = containerRef.current;
    if (!root || !sel.anchorNode || !root.contains(sel.anchorNode)) return;

    const rect = sel.getRangeAt(0).getBoundingClientRect();
    setPending({ anchorText, x: rect.left + rect.width / 2, y: rect.bottom, mode: "comment" });
    setCommentBody("");
  };

  const submitComment = async () => {
    if (!pending || !commentBody.trim() || busy) return;
    setBusy(true);
    try {
      await createSubmissionComment(submission.id, {
        anchor_text: pending.anchorText,
        body: commentBody.trim(),
      });
      toast.success("Comment added");
      cancelPending();
    } catch (e) {
      toast.error(`Could not add comment: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  /** Turn the selection into a reviewer-authored violation. It lands in the
   * same violations list as model findings, so it marks, filters, edits and
   * exports through every existing path. */
  const submitFlag = async () => {
    if (!pending || !flag.description.trim() || busy) return;
    setBusy(true);
    try {
      const created = await createReviewerViolation(submission.id, {
        current_text: pending.anchorText,
        description: flag.description.trim(),
        severity: flag.severity,
        category: flag.category,
        suggested_fix: flag.suggestedFix.trim() || undefined,
      });
      setViolations((prev) => [...prev, created]);
      cancelPending();
      window.getSelection()?.removeAllRanges();
      setAutoEditId(created.id);
      toast.success("Issue flagged");
    } catch (e) {
      // The 400 for a never-analysed submission carries a readable reason —
      // surface it rather than a generic failure.
      toast.error(`Could not flag this text: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="relative flex min-h-0 flex-1 flex-col bg-background">
      <EditorToolbar readOnly={readOnly} />

      <div className="min-h-0 flex-1 overflow-y-auto" onMouseUp={handleMouseUp}>
        <article
          ref={containerRef}
          className="prose mx-auto max-w-2xl px-8 py-10 font-serif text-[15px] leading-[1.75] text-foreground [&_p]:mb-4 [&_p]:font-sans"
        >
          {paragraphs.length === 0 && (
            <p className="text-muted-foreground">This submission has no content to display.</p>
          )}
          {paragraphs.map((pieces) => (
            <p key={pieces[0].start} className="whitespace-pre-wrap">
              {pieces.map((piece) =>
                !piece.violationId ? (
                  <React.Fragment key={piece.start}>{piece.text}</React.Fragment>
                ) : editing &&
                  editing.start === piece.start &&
                  editing.violationId === piece.violationId ? (
                  <SpanEditor
                    key={`edit-${piece.start}`}
                    target={editing}
                    violation={violationById.get(piece.violationId)}
                    onCancel={() => setEditing(null)}
                    onCommit={commitSpan}
                  />
                ) : (
                  <mark
                    key={piece.start}
                    data-violation-id={piece.violationId}
                    data-severity={normalizeSeverity(piece.severity)}
                    // Dashed underline = a reviewer wrote this flag, not the model.
                    data-source={violationById.get(piece.violationId)?.source ?? "model"}
                    title={
                      violationById.get(piece.violationId)?.source === "reviewer"
                        ? "Reviewer-added issue — click to edit this section"
                        : readOnly
                          ? undefined
                          : "Click to edit this section"
                    }
                    onClick={() => openEditor(piece)}
                  >
                    {piece.text}
                  </mark>
                )
              )}
            </p>
          ))}
        </article>
      </div>

      {pending && (
        <div
          className="fixed z-50 w-80 -translate-x-1/2 rounded-md border border-border bg-background p-2 shadow-card"
          style={{ left: pending.x, top: pending.y + 6 }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <div className="micro-label mb-1.5 text-muted-foreground">
            “{pending.anchorText.length > 80 ? `${pending.anchorText.slice(0, 80)}…` : pending.anchorText}”
          </div>

          {/* Flagging in a historical-run view is hidden on purpose: the flag
              would attach to the LATEST check, not the run on screen. */}
          {!readOnly && (
            <div className="mb-2 flex gap-1">
              {(["comment", "flag"] as const).map((m) => (
                <Button
                  key={m}
                  size="sm"
                  variant={pending.mode === m ? "default" : "ghost"}
                  aria-pressed={pending.mode === m}
                  onClick={() => setPending({ ...pending, mode: m })}
                >
                  {m === "comment" ? "Comment" : "Flag as issue"}
                </Button>
              ))}
            </div>
          )}

          {pending.mode === "comment" || readOnly ? (
            <>
              <Textarea
                autoFocus
                value={commentBody}
                onChange={(e) => setCommentBody(e.target.value)}
                className="min-h-[64px] text-xs"
                placeholder="Add a note…"
              />
              <div className="mt-2 flex justify-end gap-2">
                <Button size="sm" variant="ghost" onClick={cancelPending}>
                  Cancel
                </Button>
                <Button size="sm" disabled={!commentBody.trim() || busy} onClick={submitComment}>
                  {busy ? "Saving…" : "Comment"}
                </Button>
              </div>
            </>
          ) : (
            <>
              <div className="mb-2 flex gap-2">
                <select
                  aria-label="Severity"
                  value={flag.severity}
                  onChange={(e) => setFlag({ ...flag, severity: e.target.value })}
                  className={FIELD_CLASS}
                >
                  {FLAG_SEVERITIES.map((s) => (
                    <option key={s} value={s}>
                      {s.charAt(0).toUpperCase() + s.slice(1)}
                    </option>
                  ))}
                </select>
                <select
                  aria-label="Category"
                  value={flag.category}
                  onChange={(e) => setFlag({ ...flag, category: e.target.value })}
                  className={FIELD_CLASS}
                >
                  {categoryChoices.map((c) => (
                    <option key={c} value={c}>
                      {categoryLabel(c)}
                    </option>
                  ))}
                </select>
              </div>
              <Textarea
                autoFocus
                value={flag.description}
                onChange={(e) => setFlag({ ...flag, description: e.target.value })}
                className="min-h-[56px] text-xs"
                placeholder="What's wrong with this text?"
              />
              <Textarea
                value={flag.suggestedFix}
                onChange={(e) => setFlag({ ...flag, suggestedFix: e.target.value })}
                className="mt-1.5 min-h-[40px] text-xs"
                placeholder="Suggested fix (optional)"
              />
              <div className="mt-2 flex justify-end gap-2">
                <Button size="sm" variant="ghost" onClick={cancelPending}>
                  Cancel
                </Button>
                <Button size="sm" disabled={!flag.description.trim() || busy} onClick={submitFlag}>
                  {busy ? "Flagging…" : "Flag issue"}
                </Button>
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}

/** Dirty/saved indicator + save/revert/version-history strip. */
function EditorToolbar({ readOnly }: { readOnly?: boolean }) {
  const {
    submission,
    documentDirty,
    unsavedEdits,
    saveState,
    saveNow,
    revertToSaved,
    adoptServerText,
  } = useSubmissionWorkspace();

  return (
    <div className="flex items-center justify-between gap-3 border-b border-border bg-background px-4 py-1.5 text-xs">
      <div className="flex items-center gap-2">
        <span className="micro-label">Document</span>
        {saveState === "saving" ? (
          <span className="flex items-center gap-1 text-muted-foreground">
            <Loader2 className="h-3 w-3 animate-spin" /> Saving…
          </span>
        ) : documentDirty ? (
          <span className="flex items-center gap-1 text-sev-high">
            <TriangleAlert className="h-3 w-3" />
            {unsavedEdits || 1} unsaved change{(unsavedEdits || 1) === 1 ? "" : "s"}
            {saveState === "error" && " · save failed"}
          </span>
        ) : (
          <span className="flex items-center gap-1 text-muted-foreground">
            <Check className="h-3 w-3" /> All changes saved
          </span>
        )}
        {!readOnly && !documentDirty && (
          <span className="text-muted-foreground">
            · click a flagged section to edit it, or select any text to comment or flag it
          </span>
        )}
        {readOnly && <span className="text-muted-foreground">· read-only (historical run)</span>}
      </div>
      <div className="flex items-center gap-2">
        {documentDirty && (
          <>
            <Button size="sm" variant="outline" disabled={saveState === "saving"} onClick={saveNow}>
              Save
            </Button>
            <Button size="sm" variant="ghost" onClick={revertToSaved}>
              Discard
            </Button>
          </>
        )}
        <VersionHistoryPopover submissionId={submission.id} onRestore={adoptServerText} />
      </div>
    </div>
  );
}

/**
 * In-place editor for ONE flagged span. Pre-filled with the current text; the
 * model's suggested_fix is one click away ("fill in the blanks"), and the
 * reviewer can take it verbatim, edit it, or type their own.
 */
function SpanEditor({
  target,
  violation,
  onCancel,
  onCommit,
}: {
  target: EditTarget;
  violation?: Violation;
  onCancel: () => void;
  onCommit: (target: EditTarget, replacement: string, source: RevisionSource) => Promise<void>;
}) {
  const [value, setValue] = React.useState(target.text);
  const [busy, setBusy] = React.useState(false);
  const block = isBlockSpan(target.text) || value.includes("\n");
  const suggestion = violation?.suggested_fix?.trim() ?? "";
  // Taking the suggestion verbatim is an apply_fix; anything else is the
  // reviewer's own wording.
  const source: RevisionSource =
    suggestion && value.trim() === suggestion ? "apply_fix" : "manual_edit";

  const save = async () => {
    if (busy) return;
    if (value === target.text) {
      onCancel();
      return;
    }
    setBusy(true);
    await onCommit(target, value, source);
    setBusy(false);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onCancel();
    } else if (e.key === "Enter" && !block && !e.shiftKey) {
      e.preventDefault();
      save();
    }
  };

  const fieldClass =
    "w-full rounded-sm border border-primary bg-background px-1.5 py-1 font-sans text-[14px] " +
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary";

  return (
    <span
      className="my-1 inline-block w-full max-w-full align-top rounded-sm border border-primary/40 bg-primary-50/40 p-2 font-sans"
      onClick={(e) => e.stopPropagation()}
    >
      {block ? (
        <textarea
          autoFocus
          rows={Math.min(12, Math.max(2, value.split("\n").length + 1))}
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          className={fieldClass}
        />
      ) : (
        <input
          autoFocus
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={onKeyDown}
          className={fieldClass}
          style={{ maxWidth: "100%", width: `${Math.min(Math.max(value.length + 4, 16), 70)}ch` }}
        />
      )}

      <span className="mt-1.5 block text-[11px] leading-snug text-muted-foreground">
        <span className="micro-label">was</span> “{target.text}”
      </span>

      <span className="mt-1.5 flex flex-wrap items-center justify-between gap-2">
        <span className="flex items-center gap-1.5">
          {suggestion && (
            <Button
              size="sm"
              variant="outline"
              disabled={busy || value.trim() === suggestion}
              title={suggestion}
              onClick={() => setValue(suggestion)}
            >
              <Sparkles className="mr-1 h-3 w-3" />
              Use suggested fix
            </Button>
          )}
          {!suggestion && (
            <span className="text-[11px] text-muted-foreground">No suggested fix for this issue</span>
          )}
        </span>
        <span className="flex items-center gap-1.5">
          <span className="text-[10px] text-muted-foreground">
            {block ? "Esc cancels" : "Enter saves · Esc cancels"}
          </span>
          <Button size="sm" variant="ghost" disabled={busy} onClick={onCancel}>
            Cancel
          </Button>
          <Button size="sm" disabled={busy || value === target.text} onClick={save}>
            {busy ? "Saving…" : "Save"}
          </Button>
        </span>
      </span>
    </span>
  );
}
