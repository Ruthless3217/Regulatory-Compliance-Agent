"use client";
import * as React from "react";
import { toast } from "sonner";
import { applyHighlightsAsParagraphs } from "@/lib/highlightMarkup";
import { createSubmissionComment } from "@/lib/api";
import { useSubmissionWorkspace } from "@/components/workspace/SubmissionWorkspaceContext";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import type { Violation } from "@/lib/types";

interface Props {
  text: string;
  violations: Violation[];
  selectedViolationId: string | null;
  onSelect: (id: string) => void;
}

interface PendingComment {
  anchorText: string;
  x: number;
  y: number;
}

export function DocumentPane({ text, violations, selectedViolationId, onSelect }: Props) {
  const { submission } = useSubmissionWorkspace();
  const html = React.useMemo(
    () => applyHighlightsAsParagraphs(text || "", violations),
    [text, violations]
  );
  const containerRef = React.useRef<HTMLDivElement>(null);

  const [pending, setPending] = React.useState<PendingComment | null>(null);
  const [commentBody, setCommentBody] = React.useState("");
  const [busy, setBusy] = React.useState(false);

  // Toggle data-selected on the matching <mark>
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
  }, [selectedViolationId, html]);

  const handleClick = (e: React.MouseEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    const mark = target.closest("mark[data-violation-id]") as HTMLElement | null;
    if (mark?.dataset.violationId) onSelect(mark.dataset.violationId);
  };

  // Freestanding reviewer comments: capture whatever text the user just
  // selected as anchor_text and float a small composer near the selection.
  const cancelPending = React.useCallback(() => {
    setPending(null);
    setCommentBody("");
  }, []);

  const handleMouseUp = () => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return;
    const anchorText = sel.toString().trim();
    if (!anchorText) return;
    const root = containerRef.current;
    if (!root || !sel.anchorNode || !root.contains(sel.anchorNode)) return;

    const rect = sel.getRangeAt(0).getBoundingClientRect();
    setPending({ anchorText, x: rect.left + rect.width / 2, y: rect.bottom });
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

  return (
    <div className="relative min-h-0 flex-1 overflow-y-auto bg-background" onMouseUp={handleMouseUp}>
      <article
        ref={containerRef}
        onClick={handleClick}
        className="prose mx-auto max-w-2xl px-8 py-10 font-serif text-[15px] leading-[1.75] text-foreground [&_p]:mb-4 [&_p]:font-sans"
        dangerouslySetInnerHTML={{ __html: html || "<p class='text-muted-foreground'>This submission has no content to display.</p>" }}
      />

      {pending && (
        <div
          className="fixed z-50 w-72 -translate-x-1/2 rounded-md border border-border bg-background p-2 shadow-card"
          style={{ left: pending.x, top: pending.y + 6 }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <div className="micro-label mb-1 text-muted-foreground">
            Comment on: “{pending.anchorText.length > 80 ? `${pending.anchorText.slice(0, 80)}…` : pending.anchorText}”
          </div>
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
        </div>
      )}
    </div>
  );
}
