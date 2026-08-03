"use client";
import * as React from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { toast } from "sonner";
import { ChevronLeft, RotateCw, Download, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { ScoreRing } from "@/components/ui/score-ring";
import { StatusPill, statusTone } from "@/components/ui/status-pill";
import { analyzeSubmission, deleteSubmission } from "@/lib/api";
import { useSubmissionWorkspace } from "@/components/workspace/SubmissionWorkspaceContext";
import type { Submission } from "@/lib/types";

interface Props {
  submission: Submission;
  overallScore?: number | null;
  grade?: string | null;
}

/** Compact "Run #N of M" picker — lets a reviewer step back to a past
 * analysis run. Hidden until there's more than one run to choose between.
 * Shares its selection via SubmissionWorkspaceContext so ReviewTab's
 * historical-run banner reflects the same choice. */
function RunPicker() {
  const { runs, selectedRunId, setSelectedRunId } = useSubmissionWorkspace();
  if (runs.length <= 1) return null;

  const latest = runs[runs.length - 1];
  const current = selectedRunId ?? latest.id;

  return (
    <select
      value={current}
      onChange={(e) => setSelectedRunId(e.target.value === latest.id ? null : e.target.value)}
      title="View a past analysis run"
      className="h-5 rounded-sm border border-border bg-background px-1 font-mono text-[10px] text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
    >
      {runs.map((r) => (
        <option key={r.id} value={r.id}>
          Run #{r.run_number} of {runs.length}
          {r.id === latest.id ? " (latest)" : ""}
        </option>
      ))}
    </select>
  );
}

export function SubmissionHeader({ submission, overallScore }: Props) {
  const pathname = usePathname() ?? "";
  const router = useRouter();
  const { setOptimisticAnalyzing, setSelectedRunId } = useSubmissionWorkspace();

  const id = submission.id;
  const tab = pathname.endsWith("/report") ? "report" : "review";

  const score = overallScore ?? null;

  const rerun = async () => {
    // Optimistic: flip the shared "analyzing" flag and jump back to viewing
    // the latest run immediately, before the round trip to the server (and
    // its follow-up router.refresh()) lands the real status. Without this,
    // ReviewTab's isAnalyzing stays derived from the stale pre-rerun
    // submission.status until the next refresh actually observes 'analyzing'.
    setOptimisticAnalyzing(true);
    setSelectedRunId(null);
    try {
      await analyzeSubmission(id);
      toast.success("Re-running analysis");
      router.refresh();
    } catch (e) {
      setOptimisticAnalyzing(false);
      toast.error(`Failed: ${(e as Error).message}`);
    }
  };
  const del = async () => {
    if (!confirm("Delete this submission? This cannot be undone.")) return;
    try {
      await deleteSubmission(id);
      toast.success("Deleted");
      router.push("/");
    } catch (e) {
      toast.error(`Failed: ${(e as Error).message}`);
    }
  };

  return (
    <header className="sticky top-0 z-20 border-b border-border bg-background/85 backdrop-blur-sm">
      <div className="flex items-stretch justify-between gap-6 px-8 py-3">
        {/* Left: ring + meta */}
        <div className="flex min-w-0 items-center gap-4">
          <Link
            href="/"
            className="flex h-7 w-7 items-center justify-center rounded-sm border border-border text-muted-foreground hover:bg-muted hover:text-foreground"
            title="Back to submissions"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </Link>
          <ScoreRing score={score} size={48} strokeWidth={4} />
          <div className="min-w-0 border-l border-border pl-4">
            <div className="flex items-center gap-2 text-[11px] uppercase tracking-[0.12em] text-muted-foreground">
              <span className="font-mono">Submission</span>
              <span className="font-mono">·</span>
              <span className="font-mono">{id.slice(0, 8)}</span>
              <StatusPill tone={statusTone(submission.status)} pulse={submission.status === "analyzing"}>
                <span className="text-[10px]">{submission.status.replace(/_/g, " ")}</span>
              </StatusPill>
              <RunPicker />
            </div>
            <div className="mt-0.5 truncate text-[16px] font-semibold leading-tight tracking-tight">{submission.title}</div>
          </div>
        </div>

        {/* Right: tabs + actions */}
        <div className="flex items-center gap-3">
          <Tabs value={tab}>
            <TabsList>
              <TabsTrigger value="review" asChild>
                <Link href={`/submissions/${id}`}>Review</Link>
              </TabsTrigger>
              <TabsTrigger value="report" asChild>
                <Link href={`/submissions/${id}/report`}>Report</Link>
              </TabsTrigger>
            </TabsList>
          </Tabs>
          <div className="h-6 w-px bg-border" />
          <Button variant="outline" size="sm" onClick={rerun} title="Re-run analysis">
            <RotateCw className="h-3.5 w-3.5" />
            <span className="ml-1.5">Re-run</span>
          </Button>
          <Button asChild variant="ghost" size="sm" title="Open the printable report">
            <Link href={`/submissions/${id}/report`}>
              <Download className="h-3.5 w-3.5" />
              <span className="ml-1.5">Export</span>
            </Link>
          </Button>
          <Button variant="ghost" size="icon" onClick={del} title="Delete submission" className="text-muted-foreground hover:text-sev-critical">
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </header>
  );
}
