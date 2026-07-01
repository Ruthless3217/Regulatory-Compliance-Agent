"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { DocumentPane } from "./DocumentPane";
import { ViolationsPane } from "./ViolationsPane";
import { useSubmissionWorkspace } from "@/components/workspace/SubmissionWorkspaceContext";
import { useSSEStream } from "@/lib/sse";
import type { Violation } from "@/lib/types";

export function ReviewTab() {
 const router = useRouter();
 const {
 submission,
 violations,
 setViolations,
 selectedViolationId,
 setSelectedViolationId,
 setScore,
 } = useSubmissionWorkspace();

 const isAnalyzing =
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
 }
 } catch {
 /* ignore malformed event */
 }
 });

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
 <DocumentPane
 text={submission.original_content || ""}
 violations={violations}
 selectedViolationId={selectedViolationId}
 onSelect={setSelectedViolationId}
 />
 </div>
 <ViolationsPane
 violations={violations}
 selectedViolationId={selectedViolationId}
 setSelectedViolationId={setSelectedViolationId}
 />
 </div>
 );
}
