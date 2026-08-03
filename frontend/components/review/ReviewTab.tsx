"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { toast } from "sonner";
import { LexicalDocument } from "@/components/editor/LexicalDocument";
import { ContextRail } from "./ContextRail";
import { SplitOriginalView } from "./SplitOriginalView";
import { DocumentPane } from "./DocumentPane";
import { PdfPagePane } from "./PdfPagePane";
import { ViolationsPane } from "./ViolationsPane";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/ui/status-pill";
import { useSubmissionWorkspace } from "@/components/workspace/SubmissionWorkspaceContext";
import { useSSEStream } from "@/lib/sse";
import { diffRun, getCheck } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { RunDiff, Violation } from "@/lib/types";

/** A rail's open/closed flag, remembered across navigation.
 *
 * It always renders open and adopts the stored value in an effect: the server
 * has no localStorage, so reading it during render would hand React a first
 * client tree that differs from the one it hydrates against. Same shape as
 * DensityToggle, which reads its persisted value the same way. */
function useRailOpen(key: string) {
  const [open, setOpen] = React.useState(true);

  React.useEffect(() => {
    try {
      setOpen(window.localStorage.getItem(key) !== "collapsed");
    } catch {
      /* storage blocked — the rail just starts open every time */
    }
  }, [key]);

  const set = React.useCallback(
    (next: boolean) => {
      setOpen(next);
      try {
        window.localStorage.setItem(key, next ? "open" : "collapsed");
      } catch {
        /* storage blocked — collapsing still works, it just isn't remembered */
      }
    },
    [key]
  );

  return [open, set] as const;
}

/** One of the two side rails, collapsible to a 2rem strip so the reviewer can
 * give the document the full width while editing or comparing.
 *
 * Collapsed, the rail's content is hidden rather than unmounted — the findings
 * pane keeps its filters and scroll position across a collapse — and the strip
 * still carries the toggle (plus `badge`, the finding count), so the rail can
 * always be brought back from where it went. */
function CollapsibleRail({
  side,
  label,
  open,
  onToggle,
  badge,
  children,
}: {
  side: "left" | "right";
  label: string;
  open: boolean;
  onToggle: () => void;
  badge?: React.ReactNode;
  children: React.ReactNode;
}) {
  // The rail bodies draw their own hairline against the canvas; the strip and
  // the header row continue it so the column edge is never broken.
  const edge = side === "left" ? "border-r" : "border-l";
  // The chevron points the way the rail will move.
  const Chevron = open === (side === "left") ? ChevronLeft : ChevronRight;
  const action = `${open ? "Collapse" : "Expand"} ${label}`;

  const toggle = (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={open}
      aria-label={action}
      title={action}
      className="flex h-6 w-6 shrink-0 items-center justify-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground"
    >
      <Chevron className="h-3.5 w-3.5" />
    </button>
  );

  return (
    <div
      className={cn(
        "flex h-full min-h-0 min-w-0 flex-col bg-surface",
        !open && cn("items-center gap-1.5 border-border py-1.5", edge)
      )}
    >
      {open ? (
        <div
          className={cn(
            "flex shrink-0 items-center justify-between gap-2 border-b border-border px-2 py-1",
            edge
          )}
        >
          <span className="micro-label truncate">{label}</span>
          {toggle}
        </div>
      ) : (
        <>
          {toggle}
          {badge}
        </>
      )}
      <div className={open ? "min-h-0 flex-1" : "hidden"}>{children}</div>
    </div>
  );
}

export function ReviewTab() {
  const router = useRouter();
  const {
    submission,
    violations,
    setViolations,
    selectedViolationId,
    setSelectedViolationId,
    setScore,
    optimisticAnalyzing,
    runs,
    selectedRunId,
    setSelectedRunId,
    findingsStale,
    setLexicalDoc,
    lexicalDirty,
    saveLexical,
    saveState,
  } = useSubmissionWorkspace();

  const isAnalyzing =
    optimisticAnalyzing ||
    submission.status === "analyzing" ||
    submission.status === "preprocessing" ||
    submission.status === "uploaded";
  const ssePath = isAnalyzing ? `/compliance/analyze/${submission.id}/stream` : null;

  const [stage, setStage] = React.useState<string | null>(isAnalyzing ? "preprocess" : null);
  const [progress, setProgress] = React.useState<number>(isAnalyzing ? 0.1 : 1);

  useSSEStream(ssePath, {}, (event, data) => {
    try {
      if (event === "stage") {
        const d = JSON.parse(data);
        setStage(d.stage);
        setProgress(d.progress ?? 0);
      } else if (event === "chunk") {
        const d = JSON.parse(data);
        setViolations((prev: Violation[]) => {
          const seen = new Set(prev.map((v: Violation) => v.id));
          const next = [...prev];
          for (const v of d.violations as Violation[]) if (!seen.has(v.id)) next.push(v);
          return next;
        });
      } else if (event === "score") {
        const d = JSON.parse(data);
        setScore(d.overall_score, d.grade);
        setProgress(1);
      } else if (event === "done") {
        toast.success("Analysis complete");
        router.refresh();
      } else if (event === "error") {
        const d = JSON.parse(data);
        toast.error(`Analysis error: ${d.message}`);
        // Terminal event (analyzer exception -> failed, or needs_review):
        // without this refresh, isAnalyzing stays true forever because it's
        // derived from the frozen submission.status prop, and only `done`
        // used to clear it.
        router.refresh();
      }
    } catch {
      /* ignore malformed event */
    }
  });

  // Fallback for a silently-dropped SSE connection (proxy idle timeout,
  // network blip) that never delivers a done/error event at all: re-check
  // the real status whenever the tab regains focus while still "analyzing".
  React.useEffect(() => {
    if (!isAnalyzing) return;
    const onVisible = () => {
      if (document.visibilityState === "visible") router.refresh();
    };
    window.addEventListener("focus", onVisible);
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      window.removeEventListener("focus", onVisible);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [isAnalyzing, router]);

  // --- Historical-run viewing (GET .../runs + GET .../diff) -----------------
  const latestRun = runs.length > 0 ? runs[runs.length - 1] : null;
  const selectedRun = selectedRunId ? runs.find((r) => r.id === selectedRunId) ?? null : null;
  const isHistorical = !!selectedRun && !!latestRun && selectedRun.id !== latestRun.id;

  // The panes show the selected run's own findings while viewing history,
  // falling back to the live/latest `violations` until that fetch resolves.
  const [historicalViolations, setHistoricalViolations] = React.useState<Violation[] | null>(null);
  React.useEffect(() => {
    if (!isHistorical || !selectedRun?.compliance_check_id) {
      setHistoricalViolations(null);
      return;
    }
    let cancelled = false;
    getCheck(selectedRun.compliance_check_id)
      .then((check) => {
        if (!cancelled) setHistoricalViolations(check.violations);
      })
      .catch(() => {
        if (!cancelled) setHistoricalViolations([]);
      });
    return () => {
      cancelled = true;
    };
  }, [isHistorical, selectedRun?.compliance_check_id]);

  const displayViolations = isHistorical ? historicalViolations ?? violations : violations;

  const [diff, setDiff] = React.useState<RunDiff | null>(null);
  const [diffLoading, setDiffLoading] = React.useState(false);
  React.useEffect(() => {
    setDiff(null);
  }, [selectedRunId]);

  const toggleDiff = async () => {
    if (diff) {
      setDiff(null);
      return;
    }
    if (!selectedRun || !latestRun) return;
    setDiffLoading(true);
    try {
      setDiff(await diffRun(selectedRun.id, latestRun.id));
    } catch (e) {
      toast.error(`Could not load comparison: ${(e as Error).message}`);
    } finally {
      setDiffLoading(false);
    }
  };

  // page_render_status is the single source of truth: the backend renders every
  // format it can lay out (PDF, and DOCX via Gotenberg) and reports "skipped"
  // for the rest. Re-testing content_type here would re-close that gate.
  const pagesRendered = submission.page_render_status === "completed";
  // View (faithful render) | Split (original beside the editor) | Edit.
  // Split exists because "what did the original say?" is an audit question the
  // reviewer must be able to answer without leaving the document.
  const [mode, setMode] = React.useState<"view" | "split" | "edit">("view");
  const editing = mode !== "view";
  // Findings the editor could not place after edits. Surfaced rather than
  // dropped: a finding that is simply missing from the document reads as
  // "resolved", which is the opposite of what happened.
  const [unlocated, setUnlocated] = React.useState<Array<{ id: string; reason: string }>>([]);
  // Nothing to toggle to when there are no page images — stay on the text pane.
  const usePdfPane = pagesRendered && !editing;
  const hasEditor = !!(submission.lexical_state || submission.has_import_source);

  // --- Collapsible rails ----------------------------------------------------
  const [contextOpen, setContextOpen] = useRailOpen("review.rail.context");
  const [findingsOpen, setFindingsOpen] = useRailOpen("review.rail.findings");

  // Every selection goes through here so a finding picked in the document
  // always lands somewhere visible — selecting one and seeing nothing happen is
  // the whole failure mode of a collapsible findings rail. It opens the rail in
  // the same batch as the selection, not in an effect: the pane's own
  // scroll-to-selection effect is a child's, so it runs *before* any effect of
  // this component, and would scroll a still-hidden pane to no effect.
  const selectViolation = React.useCallback(
    (id: string | null) => {
      setSelectedViolationId(id);
      if (id) setFindingsOpen(true);
    },
    [setSelectedViolationId, setFindingsOpen]
  );

  // The count the pane itself headlines (suppressed findings are a separate
  // lane there), so the strip and the open rail can never disagree.
  const findingCount = displayViolations.filter((v) => !v.suppressed).length;

  // Tailwind needs whole class names, so the templates are spelled out rather
  // than composed. This is the only place the track widths live, which is what
  // keeps the columns and the rails they hold from drifting apart.
  const gridCols = cn(
    findingsOpen ? "grid-cols-[1fr_372px]" : "grid-cols-[1fr_2rem]",
    contextOpen
      ? findingsOpen
        ? "xl:grid-cols-[264px_1fr_372px]"
        : "xl:grid-cols-[264px_1fr_2rem]"
      : findingsOpen
        ? "xl:grid-cols-[2rem_1fr_372px]"
        : "xl:grid-cols-[2rem_1fr_2rem]"
  );

  return (
    // Three-column grammar from the workspace design: context rail, paper
    // canvas, action rail. The widths are fixed across every screen so the eye
    // never re-learns the layout — until the reviewer collapses a rail to a
    // 2rem strip and hands that width to the document. The context rail drops
    // away entirely below xl, where 264+372 of chrome would crowd the document.
    <div className={cn("grid h-full overflow-hidden rounded-md border border-border", gridCols)}>
      <div className="hidden min-h-0 xl:block">
        <CollapsibleRail
          side="left"
          label="Context"
          open={contextOpen}
          onToggle={() => setContextOpen(!contextOpen)}
        >
          <ContextRail violations={displayViolations} />
        </CollapsibleRail>
      </div>
      <div className="flex h-full min-h-0 flex-col">
        {isAnalyzing && (
          <div className="border-b border-border bg-background px-4 py-2 text-xs">
            <div className="flex items-center justify-between">
              <span className="micro-label">{stage ?? "starting"}…</span>
              <span className="font-mono text-muted-foreground">{Math.round(progress * 100)}%</span>
            </div>
            <div className="mt-1 h-1 w-full rounded-full bg-muted">
              <div
                className="h-1 rounded-full bg-primary transition-all"
                style={{ width: `${Math.round(progress * 100)}%` }}
              />
            </div>
          </div>
        )}

        {isHistorical && selectedRun && latestRun && (
          <div className="border-b border-border bg-sev-medium/5 px-4 py-2 text-xs">
            <div className="flex items-center justify-between gap-3">
              <span className="flex items-center gap-1.5">
                <StatusPill tone="warning">historical</StatusPill>
                Viewing run #{selectedRun.run_number} of {runs.length}
              </span>
              <div className="flex items-center gap-2">
                <Button size="sm" variant="ghost" onClick={() => setSelectedRunId(null)}>
                  View latest
                </Button>
                <Button size="sm" variant="outline" disabled={diffLoading} onClick={toggleDiff}>
                  {diffLoading ? "Comparing…" : diff ? "Hide comparison" : "Compare to latest"}
                </Button>
              </div>
            </div>
            {diff && (
              <div className="mt-2 space-y-1.5 rounded-sm border border-border bg-background p-2">
                <div className="font-mono text-[11px] text-muted-foreground">
                  vs run #{diff.against_run_number}: +{diff.summary.added} new, −{diff.summary.removed} resolved,{" "}
                  {diff.summary.unchanged} unchanged
                </div>
                {diff.added.length > 0 && (
                  <ul className="ml-3 list-disc space-y-0.5">
                    {diff.added.map((v) => (
                      <li key={v.id} className="text-[11px]">
                        <span className="font-medium">new:</span> {v.description}
                      </li>
                    ))}
                  </ul>
                )}
                {diff.removed.length > 0 && (
                  <ul className="ml-3 list-disc space-y-0.5">
                    {diff.removed.map((v) => (
                      <li key={v.id} className="text-[11px] text-muted-foreground line-through">
                        {v.description}
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
          </div>
        )}

        {unlocated.length > 0 && !isAnalyzing && (
          <div className="border-b border-border bg-sev-medium/5 px-4 py-2 text-xs">
            <span className="font-medium">
              {unlocated.length} finding{unlocated.length === 1 ? "" : "s"} could no longer be
              located in the edited text.
            </span>
            <span className="ml-1 text-muted-foreground">
              They are still listed on the right and still count — the text they quoted has been
              changed enough that highlighting it would be a guess. Re-run to re-anchor them.
            </span>
          </div>
        )}

        {findingsStale && !isAnalyzing && (
          <div className="border-b border-border bg-sev-high/5 px-4 py-2 text-xs">
            <span className="font-medium">Document edited since the last analysis.</span>
            <span className="ml-1 text-muted-foreground">
              The findings below describe the previous version. Re-run the compliance
              check to export.
            </span>
          </div>
        )}

        <div className="flex items-center justify-between gap-3 border-b border-border px-4 py-2">
          <div className="flex items-center gap-1">
            <Button
              size="sm"
              variant={mode === "view" ? "outline" : "ghost"}
              disabled={!pagesRendered}
              title={pagesRendered ? undefined : "No page images for this document"}
              onClick={() => setMode("view")}
            >
              View
            </Button>
            <Button
              size="sm"
              variant={mode === "split" ? "outline" : "ghost"}
              onClick={() => setMode("split")}
              title="The uploaded original beside the editable text"
            >
              Split
            </Button>
            <Button
              size="sm"
              variant={mode === "edit" ? "outline" : "ghost"}
              disabled={isHistorical}
              title={isHistorical ? "Historical runs are read-only" : undefined}
              onClick={() => setMode("edit")}
            >
              Edit
            </Button>
          </div>
          {hasEditor && !isHistorical && (
            <span className="ml-2 flex items-center gap-2 text-[11px]">
              {saveState === "saving" ? (
                <span className="text-muted-foreground">Saving…</span>
              ) : saveState === "error" ? (
                <>
                  <span className="text-sev-critical">Not saved</span>
                  <Button size="sm" variant="outline" onClick={() => void saveLexical()}>
                    Retry
                  </Button>
                </>
              ) : lexicalDirty ? (
                <>
                  <span className="text-muted-foreground">Unsaved — autosaving</span>
                  <Button size="sm" variant="ghost" onClick={() => void saveLexical()}>
                    Save now
                  </Button>
                </>
              ) : (
                <span className="text-muted-foreground">Saved</span>
              )}
            </span>
          )}
          <span className="text-[11px] text-muted-foreground">
            {submission.page_render_status === "failed"
              ? "Page render failed — showing extracted text"
              : submission.page_render_status === "skipped"
                ? "No page layout for this format — showing extracted text"
                : pagesRendered
                  ? usePdfPane
                    ? "Original formatting"
                    : "Editing extracted text"
                  : "Rendering pages…"}
          </span>
        </div>

        {mode === "split" ? (
          <SplitOriginalView
            submissionId={submission.id}
            pageRenderStatus={submission.page_render_status}
            originalText={submission.original_content}
          >
            {hasEditor ? (
              <LexicalDocument
                initialState={submission.lexical_state}
                submissionId={submission.id}
                readOnly={isHistorical}
                onChange={setLexicalDoc}
                violations={displayViolations}
                selectedViolationId={selectedViolationId}
                onSelectViolation={selectViolation}
                onUnlocatedFindings={setUnlocated}
              />
            ) : (
              <DocumentPane
                violations={displayViolations}
                selectedViolationId={selectedViolationId}
                onSelect={selectViolation}
                readOnly={isHistorical}
              />
            )}
          </SplitOriginalView>
        ) : usePdfPane ? (
          <PdfPagePane
            submissionId={submission.id}
            violations={displayViolations}
            selectedViolationId={selectedViolationId}
            onSelect={selectViolation}
          />
        ) : hasEditor ? (
          // Rich editing on the working document. The uploaded file stays
          // immutable; this edits the Lexical state and export renders from it.
          // Findings are still anchored to extracted-text offsets, which drift
          // once this editor is used — re-anchoring is Phase 2, so a submission
          // with no working document keeps the offset-highlighted pane below.
          <LexicalDocument
            initialState={submission.lexical_state}
            submissionId={submission.id}
            readOnly={isHistorical}
            onChange={setLexicalDoc}
            violations={displayViolations}
            selectedViolationId={selectedViolationId}
            onSelectViolation={selectViolation}
            onUnlocatedFindings={setUnlocated}
          />
        ) : (
          <DocumentPane
            violations={displayViolations}
            selectedViolationId={selectedViolationId}
            onSelect={selectViolation}
            readOnly={isHistorical}
          />
        )}
      </div>
      <CollapsibleRail
        side="right"
        label="Findings"
        open={findingsOpen}
        onToggle={() => setFindingsOpen(!findingsOpen)}
        badge={
          // Collapsed, the count is all that is left of the findings — without
          // it the rail hides how much work is still outstanding.
          <span
            className="rounded-sm bg-muted px-1 font-mono text-[10px] text-foreground"
            title={`${findingCount} finding${findingCount === 1 ? "" : "s"}`}
          >
            {findingCount}
          </span>
        }
      >
        <ViolationsPane
          violations={displayViolations}
          selectedViolationId={selectedViolationId}
          setSelectedViolationId={selectViolation}
        />
      </CollapsibleRail>
    </div>
  );
}
