"use client";
import * as React from "react";
import { Info, TriangleAlert } from "lucide-react";

import type { AnalysisWarning } from "@/lib/types";
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

/**
 * The notice for a run that DID grade the document, on incomplete evidence.
 *
 * Between "refused" and "certified" there is a third answer: the regulatory
 * scope was proven and the findings are real, but some product's own record,
 * the precedent corpus or a rule's scope tag was missing. The verdict is
 * already capped below "passed" server-side; this is what stops a reviewer
 * reading the score as coverage it does not have. Renders nothing when the run
 * was fully grounded.
 */
/** Which of the three kinds a warning is. Mirrors the backend's
 * `analysis_warnings.KIND_BY_CODE` for runs whose payload predates `kind`;
 * an unknown code is read as coverage — the most restrictive reading. */
const KIND_BY_CODE: Record<string, "coverage" | "tier" | "infrastructure"> = {
  rider_uins_without_fact_cards: "coverage",
  unknown_uins: "coverage",
  declared_products_without_fact_cards: "coverage",
  edition_conflicts: "coverage",
  product_grounding_budget: "coverage",
  precedent_corpus_empty: "tier",
  precedent_scope_metadata_incomplete: "tier",
  rule_scope_metadata_incomplete: "tier",
  precedent_evidence_unavailable: "tier",
  retrieval_degraded: "infrastructure",
  precedent_tier_unavailable: "infrastructure",
};

export function warningKind(w: AnalysisWarning): "coverage" | "tier" | "infrastructure" {
  if (w.kind === "coverage" || w.kind === "tier" || w.kind === "infrastructure") return w.kind;
  return KIND_BY_CODE[w.code] ?? "coverage";
}

/** The one sentence the banner may assert. Coverage wins, then
 * infrastructure, then tier — the same order as the backend's
 * `limitation_statement`, so every surface makes the same claim. */
export function limitationStatement(warnings: AnalysisWarning[]): string {
  const kinds = new Set(warnings.map(warningKind));
  if (kinds.size === 0) return "";
  if (kinds.has("coverage")) {
    return "This document was graded on incomplete evidence — the score covers less than the whole document.";
  }
  if (kinds.has("infrastructure")) {
    return "This document was graded while a retrieval component failed — every section was analysed, but evidence that retrieval would have supplied was unavailable.";
  }
  return "This document was graded with limited evidence sources — every section was analysed, but some knowledge-base evidence was unavailable.";
}

export function AnalysisWarningsBanner({
  warnings,
  className,
}: {
  warnings?: AnalysisWarning[] | null;
  className?: string;
}) {
  if (!warnings || warnings.length === 0) return null;

  return (
    <div
      role="status"
      className={cn(
        "flex shrink-0 items-start gap-2.5 border-b border-info/40 bg-info/10 px-4 py-2.5",
        className
      )}
    >
      <Info className="mt-px h-4 w-4 shrink-0 text-info" />
      <div className="text-[12.5px] leading-snug text-info">
        <p className="font-medium">{limitationStatement(warnings)}</p>
        <ul className="mt-1 space-y-0.5 opacity-90">
          {warnings.map((warning) => (
            <li key={warning.code}>
              {warning.explanation || warning.code}
              {formatWarningScope(warning) && (
                <span className="opacity-75"> {formatWarningScope(warning)}</span>
              )}{" "}
              <span className="font-mono text-[11.5px] opacity-70">
                ({warning.code})
              </span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}

/** "(UIN 116N216V01, section 25)" — which product, and where it was found. */
function formatWarningScope(warning: AnalysisWarning): string {
  const detail = warning.detail;
  if (!detail || typeof detail !== "object") return "";
  const parts: string[] = [];
  const uins = (detail as { uins?: unknown }).uins;
  if (Array.isArray(uins) && uins.length > 0) {
    parts.push(`UIN ${uins.join(", ")}`);
  }
  const chunks = (detail as { chunk_indexes?: unknown }).chunk_indexes;
  if (Array.isArray(chunks) && chunks.length > 0) {
    // 0-based on the wire; reviewers count sections from 1.
    parts.push(
      `section${chunks.length > 1 ? "s" : ""} ${chunks
        .map((index) => Number(index) + 1)
        .join(", ")}`
    );
  }
  return parts.length > 0 ? `(${parts.join(", ")})` : "";
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
