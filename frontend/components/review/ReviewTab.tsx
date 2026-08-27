"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { ChevronLeft, ChevronRight, CircleSlash, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { LexicalDocument } from "@/components/editor/LexicalDocument";
import { ContextRail } from "./ContextRail";
import { SplitOriginalView } from "./SplitOriginalView";
import { DocumentPane } from "./DocumentPane";
import { RevisionConflictBanner } from "./RevisionConflictBanner";
import { PdfPagePane } from "./PdfPagePane";
import { ViolationsPane } from "./ViolationsPane";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/ui/status-pill";
import { useSubmissionWorkspace } from "@/components/workspace/SubmissionWorkspaceContext";
import { useSSEStream } from "@/lib/sse";
import { analyzeSubmission, diffRun, getCheck } from "@/lib/api";
import { cn } from "@/lib/utils";
import type { RunDiff, Violation } from "@/lib/types";

/** A rail's open/closed flag, remembered across navigation.
 *
 * It always renders open and adopts the stored value in an effect: the server
 * has no localStorage, so reading it during render would hand React a first
 * client tree that differs from the one it hydrates against. Same shape as
 * DensityToggle, which reads its persisted value the same way. */
function useRailOpen(key: string, defaultOpen = true) {
  const [open, setOpen] = React.useState(defaultOpen);

  React.useEffect(() => {
    try {
      const stored = window.localStorage.getItem(key);
      setOpen(stored === null ? defaultOpen : stored !== "collapsed");
    } catch {
      /* storage blocked — the rail just starts at its default every time */
    }
  }, [key, defaultOpen]);

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
    // White, like the document — the canvas grey is reserved for what the panes
    // float on, so a rail painted in it reads as a hole rather than a panel.
    <div
      className={cn(
        "flex h-full min-h-0 min-w-0 flex-col border-border bg-background",
        edge,
        !open && "items-center gap-2.5 py-2.5"
      )}
    >
      {open ? (
        <div className="flex h-[38px] shrink-0 items-center justify-between gap-2 border-b border-border px-3">
          <span className="micro-label truncate">{label}</span>
          {toggle}
        </div>
      ) : (
        <>
          {toggle}
          {badge}
          {/* Vertical, so a 2rem strip still says what it is. Without it the
              collapsed rail is an unlabelled sliver and the reviewer has to
              open it to find out which one it was. */}
          <span className="micro-label [writing-mode:vertical-rl]">{label}</span>
        </>
      )}
      <div className={open ? "min-h-0 flex-1" : "hidden"}>{children}</div>
    </div>
  );
}

/** The segmented View / Split / Edit control: a track in the canvas grey with
 * the active segment lifted out of it in white. */
function ModeButton({
  active,
  disabled,
  title,
  onClick,
  children,
}: {
  active: boolean;
  disabled?: boolean;
  title?: string;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      title={title}
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "h-[26px] rounded-[4px] px-3 text-[12px] font-semibold transition-colors disabled:opacity-40",
        active
          ? "bg-background text-foreground shadow-card"
          : "text-muted-foreground hover:text-foreground"
      )}
    >
      {children}
    </button>
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
    setOptimisticAnalyzing,
    runs,
    selectedRunId,
    setSelectedRunId,
    findingsStale,
    setLexicalDoc,
    lexicalDirty,
    saveLexical,
    saveState,
    registerEditorApply,
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
  // Split's redline is on. Reported up from SplitOriginalView because it
  // decides whether the editor beside it still draws its findings.
  const [comparingDrafts, setComparingDrafts] = React.useState(false);
  // Findings as cards in the document's own margin, in both working modes.
  // Split has no rails at all; Edit has one that starts collapsed, and a
  // finding you can read beside the sentence it describes is the point of
  // editing in place. Off under the redline: a compliance span and a changed
  // word claiming the same sentence is two mark systems fighting over one
  // document, and neither reads.
  const showBubbles = editing && !comparingDrafts;

  // Same optimistic flip SubmissionHeader's Re-run does, so the banner's own
  // button and the header's button leave the workspace in the same state.
  const rerun = React.useCallback(async () => {
    setOptimisticAnalyzing(true);
    setSelectedRunId(null);
    try {
      await analyzeSubmission(submission.id);
      toast.success("Re-running analysis");
      router.refresh();
    } catch (e) {
      toast.error(`Could not start the analysis: ${(e as Error).message}`);
      setOptimisticAnalyzing(false);
    }
  }, [submission.id, setOptimisticAnalyzing, setSelectedRunId, router]);
  // Nothing to toggle to when there are no page images — stay on the text pane.
  const usePdfPane = pagesRendered && !editing;
  const hasEditor = !!(submission.lexical_state || submission.has_import_source);

  // --- Collapsible rails ----------------------------------------------------
  const [contextOpen, setContextOpen] = useRailOpen("review.rail.context");
  const [readFindingsOpen, setReadFindingsOpen] = useRailOpen("review.rail.findings");
  // Editing hands the findings to the document's own margin (LexicalDocument
  // `bubbles`), so the rail starts out of the way there — and remembers that
  // separately, so putting it away while editing does not also put it away
  // while reading.
  const [editFindingsOpen, setEditFindingsOpen] = useRailOpen("review.rail.findings.edit", false);
  const findingsOpen = mode === "edit" ? editFindingsOpen : readFindingsOpen;
  const setFindingsOpen = mode === "edit" ? setEditFindingsOpen : setReadFindingsOpen;

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

  // Split puts two documents on one screen. Both rails come off entirely —
  // not collapsed to their strips — because 264+372 of chrome around two
  // half-width documents leaves neither readable, and the findings the right
  // rail held move into the editor's own margin as bubbles beside the text
  // they describe (LexicalDocument `bubbles`). The rails' own open/closed
  // state is untouched, so leaving Split restores exactly what was there.
  const splitting = mode === "split";

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
    <div
      className={cn(
        "grid h-full overflow-hidden bg-surface",
        splitting ? "grid-cols-[1fr]" : gridCols
      )}
    >
      {!splitting && (
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
      )}
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

        {/* One notice, not two. Both of these say "the document moved on since
            the analysis" and both are fixed by the same re-run, so stacking
            them spent two rows above the document to deliver one instruction —
            on a screen whose whole job is showing the document. The action that
            resolves them sits in the notice rather than in the header, because
            that is where the reviewer is reading. */}
        {(findingsStale || unlocated.length > 0) && !isAnalyzing && (
          <div
            className={cn(
              "flex shrink-0 items-center gap-2.5 border-b px-4 py-2.5",
              findingsStale
                ? "border-warning/40 bg-warning/10"
                : "border-border bg-background"
            )}
          >
            {findingsStale ? (
              <TriangleAlert className="h-4 w-4 shrink-0 text-warning-fg" />
            ) : (
              <CircleSlash className="h-4 w-4 shrink-0 text-muted-foreground" />
            )}
            <span
              className={cn(
                "text-[12.5px] leading-snug",
                findingsStale ? "text-warning-fg" : "text-body"
              )}
            >
              {findingsStale && (
                <span className="font-medium">
                  Document edited since the last analysis — these findings describe the previous
                  version.{" "}
                </span>
              )}
              {unlocated.length > 0 && (
                <span className={findingsStale ? undefined : "font-medium"}>
                  {unlocated.length} finding{unlocated.length === 1 ? "" : "s"} can no longer be
                  located in the text and {unlocated.length === 1 ? "is" : "are"} not highlighted.{" "}
                </span>
              )}
              <span className={findingsStale ? "opacity-80" : "text-muted-foreground"}>
                {unlocated.length > 0 && "They still count. "}
                Re-run the compliance check to re-anchor
                {findingsStale ? " and to approve or export." : "."}
              </span>
            </span>
            <Button
              size="sm"
              variant={findingsStale ? "default" : "outline"}
              className="ml-auto shrink-0"
              onClick={rerun}
            >
              Re-run analysis
            </Button>
          </div>
        )}

        {/* Mode bar. Fixed 40px so the document below starts at the same line
            whichever mode is showing, and the segmented control reads as one
            control rather than three buttons that happen to sit together. */}
        <div className="flex h-10 shrink-0 items-center gap-3 border-b border-border bg-background px-3">
          <div className="flex shrink-0 gap-0.5 rounded-[5px] bg-surface p-[3px]">
            <ModeButton
              active={mode === "view"}
              disabled={!pagesRendered}
              title={pagesRendered ? undefined : "No page images for this document"}
              onClick={() => setMode("view")}
            >
              View
            </ModeButton>
            <ModeButton
              active={mode === "split"}
              title="The uploaded original beside the editable text"
              onClick={() => setMode("split")}
            >
              Split
            </ModeButton>
            <ModeButton
              active={mode === "edit"}
              disabled={isHistorical}
              title={isHistorical ? "Historical runs are read-only" : undefined}
              onClick={() => setMode("edit")}
            >
              Edit
            </ModeButton>
          </div>
          <span className="truncate text-[11.5px] text-faint">
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
          {hasEditor && !isHistorical && (
            <span className="ml-auto flex shrink-0 items-center gap-2.5 text-[11.5px]">
              {/* A dot before the word: the state is glanceable at colour, and
                  the word is there for anyone who does not read colour. */}
              <span className="flex items-center gap-2 text-muted-foreground">
                <span
                  className={cn(
                    "h-[7px] w-[7px] rounded-full",
                    saveState === "error" || saveState === "conflict"
                      ? "bg-sev-critical"
                      : saveState === "saving"
                        ? "bg-warning"
                        : lexicalDirty
                          ? "bg-warning"
                          : "bg-success"
                  )}
                />
                {saveState === "conflict"
                  ? "Not saved — conflict"
                  : saveState === "saving"
                  ? "Saving…"
                  : saveState === "error"
                    ? "Not saved"
                    : lexicalDirty
                      ? "Unsaved — autosaving"
                      : "All changes saved"}
              </span>
              {saveState !== "conflict" && (saveState === "error" || lexicalDirty) && (
                <Button size="sm" variant="outline" onClick={() => void saveLexical()}>
                  {saveState === "error" ? "Retry" : "Save now"}
                </Button>
              )}
            </span>
          )}
        </div>

        <RevisionConflictBanner />

        {/* View is the only mode with no working document on screen, so it is
            the only one that may replace this subtree. Split and Edit both
            render through SplitOriginalView — see its `split` prop: moving the
            editor between two parents remounts it, and a remounted editor comes
            up empty, reports every finding as unlocatable, and used to emit
            that empty document straight into the autosave. */}
        {usePdfPane ? (
          <PdfPagePane
            submissionId={submission.id}
            violations={displayViolations}
            selectedViolationId={selectedViolationId}
            onSelect={selectViolation}
          />
        ) : (
          <SplitOriginalView
            split={mode === "split"}
            submissionId={submission.id}
            pageRenderStatus={submission.page_render_status}
            originalText={submission.original_content}
            onCompareChange={setComparingDrafts}
          >
            {hasEditor ? (
              // Rich editing on the working document. The uploaded file stays
              // immutable; this edits the Lexical state and export renders from
              // it.
              //
              // `has_import_source` only promises the upload *looks* importable,
              // so this can mount over a conversion that then fails; the editor
              // says so rather than showing a blank page, and needs
              // pagesRendered to know whether View is one of the ways out it can
              // offer.
              <LexicalDocument
                initialState={submission.lexical_state}
                submissionId={submission.id}
                readOnly={isHistorical}
                onChange={setLexicalDoc}
                // Findings come off while the redline is on: a compliance span
                // and a changed word claiming the same sentence is two mark
                // systems fighting over one document, and neither reads.
                violations={comparingDrafts ? undefined : displayViolations}
                selectedViolationId={selectedViolationId}
                // With cards in the margin the finding is already on screen
                // where it was clicked; forcing the rail open would shove the
                // document aside to show a second copy of what the reviewer is
                // looking at. Without them, the rail IS where the finding is.
                onSelectViolation={showBubbles ? setSelectedViolationId : selectViolation}
                onUnlocatedFindings={setUnlocated}
                pagesRendered={pagesRendered}
                bubbles={showBubbles}
                chromeless={mode === "split"}
                registerApply={registerEditorApply}
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
        )}
      </div>
      {!splitting && (
        <CollapsibleRail
          side="right"
          label="Findings"
          open={findingsOpen}
          onToggle={() => setFindingsOpen(!findingsOpen)}
          badge={
            // Collapsed, the count is all that is left of the findings — without
            // it the rail hides how much work is still outstanding. Carried in
            // the critical colour: the number is the outstanding work, and a
            // muted chip reads as a decoration rather than a queue.
            <span
              className="font-mono text-[12px] font-medium text-sev-critical"
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
      )}
    </div>
  );
}
