"use client";
import * as React from "react";
import { Button } from "@/components/ui/button";
import { submissionPageImageUrl } from "@/lib/api";

/**
 * Two-pane original-vs-working view: the immutable uploaded file on the left,
 * the editable working document (`children`) on the right.
 *
 * The left pane never renders blank. It shows, in priority order: the rendered
 * page images, else the extracted text explicitly labelled as a fallback, else
 * a statement of what is missing. A blank sheet would read as "the document is
 * empty", which is the opposite of "we could not render it".
 */
export function SplitOriginalView({
  submissionId,
  pageRenderStatus,
  originalText,
  children,
}: {
  submissionId: string;
  pageRenderStatus?: string | null;
  originalText?: string | null;
  children: React.ReactNode;
}): React.ReactElement {
  return (
    // Two columns of equal weight on the canvas, each under its own label, so
    // which side is the immutable original and which is the working copy is
    // answered before the reviewer starts comparing wording.
    <div className="grid h-full min-h-0 grid-cols-2 gap-4 overflow-hidden bg-surface px-4 pt-3">
      <div className="flex min-h-0 flex-col gap-2 overflow-hidden">
        <div className="flex shrink-0 items-center gap-2">
          <span className="micro-label">Original · uploaded file</span>
          {/* Not decoration: nothing in the app writes to the uploaded file.
              The pane asserts the guarantee so "why can't I type here" never
              becomes a support question. */}
          <span className="font-mono text-[10.5px] text-faint">read-only · immutable</span>
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-border bg-background shadow-card">
          <OriginalPane
            submissionId={submissionId}
            pageRenderStatus={pageRenderStatus}
            originalText={originalText}
          />
        </div>
      </div>
      <div className="flex min-h-0 flex-col gap-2 overflow-hidden">
        <div className="flex shrink-0 items-center gap-2">
          <span className="micro-label">Working copy · editable</span>
          <span className="font-mono text-[10.5px] text-faint">exports from this side</span>
        </div>
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-border bg-background shadow-card">
          {children}
        </div>
      </div>
    </div>
  );
}

function OriginalPane({
  submissionId,
  pageRenderStatus,
  originalText,
}: {
  submissionId: string;
  pageRenderStatus?: string | null;
  originalText?: string | null;
}) {
  const [page, setPage] = React.useState(1);
  // Set once a page 404s: that page does not exist, so the one before it was
  // the last. Probing stops permanently at that point.
  const [lastPage, setLastPage] = React.useState<number | null>(null);
  // page 1 itself 404ing means "completed" but no files — fall through to text
  // rather than showing an empty sheet.
  const [noImages, setNoImages] = React.useState(false);

  const showImages = pageRenderStatus === "completed" && !noImages;
  const hasNext = lastPage === null || page < lastPage;
  const text = originalText?.trim();

  if (showImages) {
    return (
      <>
        <div className="min-h-0 flex-1 overflow-y-auto bg-background p-4">
          <div className="mx-auto max-w-3xl border border-border bg-background shadow-sm">
            <img
              key={page}
              src={submissionPageImageUrl(submissionId, page)}
              alt={`Original page ${page}`}
              draggable={false}
              className="block w-full select-none"
              onError={() => (page === 1 ? setNoImages(true) : setLastPage(page - 1))}
            />
          </div>
          {/* One page ahead, hidden: its 404 is what ends the range, so Next is
              already disabled by the time the reviewer reaches the last page.
              Only ever one probe in flight, and none once lastPage is known. */}
          {lastPage === null && (
            <img
              src={submissionPageImageUrl(submissionId, page + 1)}
              alt=""
              aria-hidden
              className="hidden"
              onError={() => setLastPage(page)}
            />
          )}
        </div>
        <div className="flex items-center justify-center gap-3 border-t border-border px-3 py-1.5">
          <Button size="sm" variant="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>
            Previous
          </Button>
          <span className="font-mono text-[11px] text-muted-foreground">
            page {page}
            {lastPage !== null && ` of ${lastPage}`}
          </span>
          <Button size="sm" variant="ghost" disabled={!hasNext} onClick={() => setPage(page + 1)}>
            Next
          </Button>
        </div>
      </>
    );
  }

  return (
    <div className="min-h-0 flex-1 overflow-y-auto p-4">
      <p className="mb-3 rounded-sm border border-border bg-background px-2.5 py-1.5 text-[11px] text-muted-foreground">
        {pageRenderStatus === "failed"
          ? "The original could not be rendered as page images."
          : pageRenderStatus === "completed"
            ? "The rendered page images for this document are missing."
            : pageRenderStatus === "skipped"
              ? "This format has no page layout to render."
              : "Page images are not available."}{" "}
        {text
          ? "Showing the extracted text instead — this is a fallback, not the page image, and does not reproduce the original layout."
          : "There is no extracted text for this document either, so the original cannot be shown here. Download the uploaded file to read it."}
      </p>
      {text && (
        <pre className="whitespace-pre-wrap break-words font-mono text-[13px] leading-relaxed">
          {text}
        </pre>
      )}
    </div>
  );
}
