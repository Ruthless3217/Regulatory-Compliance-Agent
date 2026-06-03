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
import type { Submission } from "@/lib/types";

interface Props {
  submission: Submission;
  overallScore?: number | null;
  grade?: string | null;
}

export function SubmissionHeader({ submission, overallScore, grade: _grade }: Props) {
  const pathname = usePathname() ?? "";
  const router = useRouter();

  const id = submission.id;
  const tab = pathname.endsWith("/report")
    ? "report"
    : pathname.endsWith("/chat")
      ? "chat"
      : "review";

  const score = overallScore ?? null;

  const rerun = async () => {
    try {
      await analyzeSubmission(id);
      toast.success("Re-running analysis");
      router.refresh();
    } catch (e) {
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
              <TabsTrigger value="chat" asChild>
                <Link href={`/submissions/${id}/chat`}>Chat</Link>
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
