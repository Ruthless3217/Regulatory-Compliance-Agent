"use client";

import * as React from "react";
import Link from "next/link";
import { ArrowRight, Check, Loader2, ShieldCheck } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { simulateAnalyze } from "@/lib/mockApi";
import type { SSEAnalyzeStage } from "@/lib/types";

const STAGES: { key: SSEAnalyzeStage["stage"]; label: string }[] = [
  { key: "preprocess", label: "Preprocess" },
  { key: "dispatch", label: "Dispatch" },
  { key: "analysis", label: "Analysis" },
  { key: "scoring", label: "Scoring" },
];

/** Drives `simulateAnalyze` and renders a quiet stage stepper + progress bar, ending in a "View report" CTA. */
export function AnalyzeProgress({ submissionId }: { submissionId: string }) {
  const [stageIndex, setStageIndex] = React.useState(-1);
  const [progress, setProgress] = React.useState(0);
  const [findingCount, setFindingCount] = React.useState(0);
  const [done, setDone] = React.useState(false);

  React.useEffect(() => {
    let cancelled = false;

    async function run() {
      for await (const ev of simulateAnalyze(submissionId)) {
        if (cancelled) return;
        if ("stage" in ev) {
          setStageIndex(STAGES.findIndex((s) => s.key === ev.stage));
          setProgress(ev.progress);
        } else if ("chunk_index" in ev) {
          setFindingCount((c) => c + ev.violations.length);
        } else if ("done" in ev) {
          setProgress(100);
          setStageIndex(STAGES.length);
          setDone(true);
        }
        // SSEAnalyzeScore ("overall_score") events are consumed by the report screen, not shown here.
      }
    }

    run();
    return () => {
      cancelled = true;
    };
  }, [submissionId]);

  const currentLabel = done ? "Done" : STAGES[stageIndex]?.label ?? "Starting…";

  return (
    <Card className="animate-fade-in">
      <CardHeader>
        <CardTitle className="text-base">{done ? "Analysis complete" : "Analyzing submission"}</CardTitle>
        <CardDescription>
          {done
            ? "Compliance findings are ready to review."
            : "Running preprocessing, rule dispatch, LLM analysis, and scoring."}
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-6">
        <div className="space-y-2">
          <div className="h-1.5 w-full overflow-hidden rounded-full bg-muted">
            <div
              className="h-full rounded-full bg-primary transition-all duration-500 ease-out"
              style={{ width: `${progress}%` }}
            />
          </div>
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>{currentLabel}</span>
            <span className="font-mono tabular-nums">{progress}%</span>
          </div>
        </div>

        <ol className="grid grid-cols-4 gap-2">
          {STAGES.map((stage, idx) => {
            const status = done || idx < stageIndex ? "complete" : idx === stageIndex ? "active" : "pending";
            return (
              <li key={stage.key} className="flex flex-col items-center gap-1.5 text-center">
                <span
                  className={cn(
                    "flex h-7 w-7 items-center justify-center rounded-full border text-xs transition-colors duration-300",
                    status === "complete" && "border-primary bg-primary text-primary-foreground",
                    status === "active" && "border-primary text-primary",
                    status === "pending" && "border-border text-muted-foreground"
                  )}
                >
                  {status === "complete" ? (
                    <Check className="h-3.5 w-3.5 animate-fade-in" />
                  ) : status === "active" ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    idx + 1
                  )}
                </span>
                <span className={cn("text-[11px]", status === "pending" ? "text-muted-foreground" : "text-foreground")}>
                  {stage.label}
                </span>
              </li>
            );
          })}
        </ol>

        {!done && findingCount > 0 && (
          <p className="text-xs text-muted-foreground">
            <span className="font-mono tabular-nums">{findingCount}</span> potential findings surfaced so far…
          </p>
        )}

        {done && (
          <div className="flex flex-col items-start justify-between gap-3 rounded-md border border-border bg-muted/40 px-4 py-3 sm:flex-row sm:items-center">
            <div className="flex items-center gap-2 text-sm">
              <ShieldCheck className="h-4 w-4 text-success" />
              <span>
                <span className="font-mono tabular-nums">{findingCount}</span> findings ready for review
              </span>
            </div>
            <Button asChild size="sm">
              <Link href={`/submissions/${submissionId}/report`}>
                View report <ArrowRight className="h-3.5 w-3.5" />
              </Link>
            </Button>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
