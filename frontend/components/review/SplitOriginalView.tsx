"use client";
import * as React from "react";
import { GitCompare, Image as ImageIcon, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { DiffViewer } from "@/components/compare/DiffViewer";
import { getSubmissionDraftDiff, submissionPageImageUrl } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { DiffBlock } from "@/lib/types";

/**
 * Split: the uploaded original beside the editable working copy.
 *
 * The question this mode answers is "what did we change, and is the new draft
 * better than the old one?" — a question about two drafts. So both sides are
 * rendered as DOCUMENTS, in the same serif at the same measure, and the
 * original defaults to its extracted text rather than the rendered page
 * images: a page image is a picture of a PDF, which cannot be read against
 * editable prose line for line, and View already exists for anyone who wants
 * the original artwork. The page images stay one click away rather than being
 * removed, because "what did the artwork actually look like" is still a real
 * question — just not this mode's question.
 *
 * Compare turns the pair into a redline of the reviewer's own corrections,
 * using the same aligner Compare uses between two files
 * (GET /submissions/{id}/draft-diff).
 */
export function SplitOriginalView({
  submissionId,
  pageRenderStatus,
  originalText,
  /** Turned off while the redline is on — see the note on `compare` below. */
  onCompareChange,
  /**
   * False in Edit mode: the original pane and the compare bar come off, and the
   * working copy takes the full width.
   *
   * This component wraps the editor in BOTH modes rather than only in Split,
   * and that is load-bearing, not tidiness. React reconciles by position, so
   * rendering the editor as a child here in one mode and as a sibling in the
   * other unmounts and remounts it on every switch. A remounted editor comes up
   * empty while it re-fetches its import, reports every finding as unlocatable
   * for those seconds, and emits an empty document into the workspace — which
   * the autosave then wrote over the reviewer's working copy.
   */
  split = true,
  children,
}: {
  submissionId: string;
  pageRenderStatus?: string | null;
  originalText?: string | null;
  onCompareChange?: (comparing: boolean) => void;
  split?: boolean;
  children: React.ReactNode;
}): React.ReactElement {
  const [compare, setCompare] = React.useState(false);
  const [pages, setPages] = React.useState(false);

  // Leaving Split closes the redline with it, so returning to Edit never lands
  // on a document whose findings are silently switched off.
  React.useEffect(() => {
    if (!split) setCompare(false);
  }, [split]);

  // Two mark systems over one document is the "unnecessary highlighting"
  // problem: a compliance span and a redline word both claim the same
  // sentence and neither reads. While the redline is on, the findings
  // decorations come off.
  React.useEffect(() => onCompareChange?.(compare), [compare, onCompareChange]);

  const canShowPages = pageRenderStatus === "completed";

  return (
    <div className={cn("flex h-full min-h-0 flex-col", split && "bg-surface")}>
      {split && (
        <div className="flex h-9 shrink-0 items-center gap-2 border-b border-border bg-background px-4">
          <span className="micro-label">Original vs working copy</span>
          <div className="ml-auto flex items-center gap-1.5">
            {canShowPages && !compare && (
              <Button
                size="sm"
                variant={pages ? "outline" : "ghost"}
                aria-pressed={pages}
                title="Show the original as its rendered page images instead of text"
                onClick={() => setPages((p) => !p)}
              >
                <ImageIcon className="mr-1.5 h-3.5 w-3.5" />
                Original artwork
              </Button>
            )}
            <Button
              size="sm"
              variant={compare ? "default" : "outline"}
              aria-pressed={compare}
              title="Redline the corrections made to this document"
              onClick={() => setCompare((c) => !c)}
            >
              <GitCompare className="mr-1.5 h-3.5 w-3.5" />
              {compare ? "Hide changes" : "Compare drafts"}
            </Button>
          </div>
        </div>
      )}

      {split && compare && <DraftRedline submissionId={submissionId} />}

      {/* Hidden rather than unmounted while the redline is up, and keyed so the
          working-copy column keeps its identity when the original column comes
          and goes. Both are the same rule: the editor inside must never be
          torn down by a layout change. */}
      <div
        className={cn(
          "grid min-h-0 flex-1 overflow-hidden",
          split ? "grid-cols-2 gap-4 px-4 pt-3" : "grid-cols-1",
          split && compare && "hidden"
        )}
      >
        {split && (
          <div key="original" className="flex min-h-0 flex-col gap-2 overflow-hidden">
            <div className="flex shrink-0 items-center gap-2">
              <span className="micro-label">Original · uploaded file</span>
              <span className="font-mono text-[10.5px] text-faint">read-only · immutable</span>
            </div>
            <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-md border border-border bg-background shadow-card">
              <OriginalPane
                submissionId={submissionId}
                pageRenderStatus={pageRenderStatus}
                originalText={originalText}
                pages={pages && canShowPages}
              />
            </div>
          </div>
        )}
        <div key="working" className="flex min-h-0 flex-col gap-2 overflow-hidden">
          {split && (
            <div className="flex shrink-0 items-center gap-2">
              <span className="micro-label">Working copy · editable</span>
              <span className="font-mono text-[10.5px] text-faint">exports from this side</span>
            </div>
          )}
          <div
            className={cn(
              "flex min-h-0 flex-1 flex-col overflow-hidden",
              split && "rounded-md border border-border bg-background shadow-card"
            )}
          >
            {children}
          </div>
        </div>
      </div>
    </div>
  );
}

/** The corrections themselves, word-aligned. */
function DraftRedline({ submissionId }: { submissionId: string }) {
  const [data, setData] = React.useState<{
    blocks: DiffBlock[];
    changed: number;
    edited: boolean;
  } | null>(null);
  const [err, setErr] = React.useState<string | null>(null);
  const [selected, setSelected] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    setErr(null);
    setData(null);
    getSubmissionDraftDiff(submissionId)
      .then((r) => !cancelled && setData(r))
      .catch((e) => !cancelled && setErr((e as Error).message));
    return () => {
      cancelled = true;
    };
  }, [submissionId]);

  return (
    <div className="min-h-0 flex-1 overflow-y-auto px-4 py-3">
      <div className="mx-auto max-w-[1240px]">
        {err ? (
          <p className="rounded-md border border-border bg-background px-4 py-8 text-center text-[12.5px] text-muted-foreground">
            {err}
          </p>
        ) : !data ? (
          <p className="flex items-center justify-center gap-2 rounded-md border border-border bg-background px-4 py-8 text-[12.5px] text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Aligning the two drafts…
          </p>
        ) : !data.edited ? (
          <p className="rounded-md border border-border bg-background px-4 py-8 text-center text-[12.5px] text-muted-foreground">
            The working copy is still identical to the uploaded original — nothing has been
            corrected yet, so there is no redline to show.
          </p>
        ) : (
          <>
            <p className="mb-2.5 text-[12px] text-muted-foreground">
              <span className="font-medium text-foreground">{data.changed}</span> changed
              {data.changed === 1 ? " passage" : " passages"} between the uploaded original and the
              working copy.
            </p>
            <DiffViewer
              blocks={data.blocks}
              oldLabel="Original · uploaded file"
              newLabel="Working copy · editable"
              selectedId={selected}
              onSelect={setSelected}
            />
          </>
        )}
      </div>
    </div>
  );
}

function OriginalPane({
  submissionId,
  pageRenderStatus,
  originalText,
  pages,
}: {
  submissionId: string;
  pageRenderStatus?: string | null;
  originalText?: string | null;
  pages: boolean;
}) {
  const [page, setPage] = React.useState(1);
  // Set once a page 404s: that page does not exist, so the one before it was
  // the last. Probing stops permanently at that point.
  const [lastPage, setLastPage] = React.useState<number | null>(null);
  const [noImages, setNoImages] = React.useState(false);

  const hasNext = lastPage === null || page < lastPage;
  const text = originalText?.trim();

  if (pages && !noImages) {
    return (
      <>
        <div className="min-h-0 flex-1 overflow-y-auto bg-surface p-4">
          <div className="mx-auto border border-border bg-background shadow-sm">
            {/* eslint-disable-next-line @next/next/no-img-element */}
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
              already disabled by the time the reviewer reaches the last page. */}
          {lastPage === null && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={submissionPageImageUrl(submissionId, page + 1)}
              alt=""
              aria-hidden
              className="hidden"
              onError={() => setLastPage(page)}
            />
          )}
        </div>
        <div className="flex shrink-0 items-center justify-center gap-3 border-t border-border px-3 py-1.5">
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

  if (!text) {
    return (
      <div className="min-h-0 flex-1 overflow-y-auto p-6">
        <p className="text-[12.5px] leading-relaxed text-muted-foreground">
          {pageRenderStatus === "completed"
            ? "The rendered page images for this document are missing, and there is no extracted text either."
            : "There is no extracted text for this document, so the original cannot be shown here."}{" "}
          Download the uploaded file to read it.
        </p>
      </div>
    );
  }

  return (
    // Same typography as the working copy opposite it. Two documents set
    // differently cannot be compared by eye — every difference in weight or
    // measure reads as a difference in content.
    <div className="min-h-0 flex-1 overflow-y-auto px-10 py-8">
      <div
        className={cn(
          "font-serif text-[15.5px] leading-[1.78] text-foreground",
          "[&>p]:mb-[18px] [&>p:last-child]:mb-0"
        )}
      >
        {text.split(/\n\s*\n/).map((para, i) => (
          <p key={i} className="whitespace-pre-wrap">
            {para}
          </p>
        ))}
      </div>
    </div>
  );
}
