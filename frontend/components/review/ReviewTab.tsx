"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { DocumentPane } from "./DocumentPane";
import { PdfPagePane } from "./PdfPagePane";
import { ViolationsPane } from "./ViolationsPane";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/ui/status-pill";
import { useSubmissionWorkspace } from "@/components/workspace/SubmissionWorkspaceContext";
import { useSSEStream } from "@/lib/sse";
import { diffRun, getCheck } from "@/lib/api";
import type { RunDiff, Violation } from "@/lib/types";

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

  const usePdfPane = submission.content_type === "pdf" && submission.page_render_status === "completed";

  return (
    <div className="grid h-full grid-cols-[1fr_400px] overflow-hidden rounded-md border border-border">
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

        {usePdfPane ? (
          <PdfPagePane
            submissionId={submission.id}
            violations={displayViolations}
            selectedViolationId={selectedViolationId}
            onSelect={setSelectedViolationId}
          />
        ) : (
          <DocumentPane
            violations={displayViolations}
            selectedViolationId={selectedViolationId}
            onSelect={setSelectedViolationId}
            readOnly={isHistorical}
          />
        )}
      </div>
      <ViolationsPane
        violations={displayViolations}
        selectedViolationId={selectedViolationId}
        setSelectedViolationId={setSelectedViolationId}
      />
    </div>
  );
}
