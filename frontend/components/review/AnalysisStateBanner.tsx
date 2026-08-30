"use client";
import * as React from "react";
import { TriangleAlert } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * The notice for a run that finished without producing a grade.
 *
 * `evaluate_persistability` can refuse to persist a completed analysis — an
 * unresolvable product, an ambiguous UIN, a degraded disclosure sweep. The
 * refusal is deliberate and correct, but it writes no ComplianceCheck, so the
 * workspace it leaves behind is indistinguishable from a document nobody has
 * analysed yet. This is the difference: it says the pipeline reached a verdict,
 * that the verdict is "not graded", and why.
 */

export interface AnalysisStateInput {
  /** `analysis_state` from GET /compliance/results — the authoritative signal. */
  analysisState?: string | null;
  /** submissions.status, kept as the pre-`analysis_state` fallback. */
  analysisStatus?: string | null;
  /** Present whenever the results endpoint returned no check. */
  analysisMessage?: string | null;
}

/** True only when a run reached a terminal state without producing a grade. */
export function analysisIsIncomplete({
  analysisState,
  analysisStatus,
  analysisMessage,
}: AnalysisStateInput): boolean {
  if (analysisState) {
    return analysisState === "needs_review" || analysisState === "failed";
  }
  // Older backend (no `analysis_state` in the payload): fall back to the
  // signal this used to be derived from, so a version skew cannot silently
  // remove the warning. `not_analyzed` is indistinguishable here — that was
  // the pre-existing false positive, and it is the safe direction.
  return (
    !!analysisMessage ||
    analysisStatus === "failed" ||
    analysisStatus === "waiting_for_review"
  );
}

export function AnalysisStateBanner({
  degradedReason,
  className,
  ...state
}: AnalysisStateInput & {
  /** AnalysisRun.degraded_reason — the internal token for the refusal. */
  degradedReason?: string | null;
  className?: string;
}) {
  if (!analysisIsIncomplete(state)) return null;

  return (
    <div
      role="status"
      className={cn(
        "flex shrink-0 items-start gap-2.5 border-b border-warning/40 bg-warning/10 px-4 py-2.5",
        className
      )}
    >
      <TriangleAlert className="mt-px h-4 w-4 shrink-0 text-warning-fg" />
      <div className="text-[12.5px] leading-snug text-warning-fg">
        <p className="font-medium">
          This document has NOT been graded as compliant — the compliance check
          finished without a result.
        </p>
        <p className="mt-0.5 opacity-80">
          {state.analysisMessage ??
            "The analysis was incomplete or degraded. Send it for manual review before relying on this document."}
          {degradedReason && (
            <>
              {" "}
              <span className="font-mono opacity-90">({degradedReason})</span>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
