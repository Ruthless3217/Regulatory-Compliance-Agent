"use client";
import * as React from "react";
import type { Violation } from "@/lib/types";

/**
 * Shows the provenance behind a violation, across the three grounding tiers:
 *  - precedent → the historical reviewer's actual comment on a matching past
 *    document, the phrase it was flagged on, and the source ticket.
 *  - rule      → the regulator passage (verbatim) and the rule id it cites.
 *  - novel     → the regulatory basis (no historical match to point at).
 *
 * Used on both the Review tab (ViolationCard) and the report (ViolationGroup).
 */
export function PrecedentNote({ violation }: { violation: Violation }) {
  const meta = violation.violation_metadata ?? null;
  const grounding = meta?.grounding;
  const regulatoryBasis = meta?.regulatory_basis;

  if (grounding !== "novel" && grounding !== "rule" && violation.cited_comment_verbatim) {
    const matchPct =
      typeof violation.similarity_score === "number"
        ? Math.round(violation.similarity_score * 100)
        : null;
    return (
      <div className="rounded-sm border border-primary/30 bg-primary-50/50 p-2 text-xs">
        <div className="mb-1 flex items-center justify-between">
          <span className="micro-label text-primary">Compliance reviewer note</span>
          {matchPct !== null && (
            <span className="font-mono text-[11px] text-muted-foreground">
              {matchPct}% match
            </span>
          )}
        </div>
        <p className="leading-snug">{violation.cited_comment_verbatim}</p>
        {violation.cited_anchor_text && (
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            Flagged on: &ldquo;{violation.cited_anchor_text}&rdquo;
          </p>
        )}
        {violation.cited_final_text && (
          <div className="mt-2 rounded-sm border border-emerald-300/60 bg-emerald-50/60 p-1.5">
            <div className="micro-label mb-0.5 text-emerald-700">
              Approved rewrite from this past case
            </div>
            <p className="leading-snug text-emerald-900">{violation.cited_final_text}</p>
          </div>
        )}
        {(violation.cited_source_file || violation.cited_document_id) && (
          <p className="mt-1 font-mono text-[11px] text-muted-foreground">
            Precedent: {violation.cited_source_file ?? violation.cited_document_id}
          </p>
        )}
      </div>
    );
  }

  if (grounding === "rule") {
    return (
      <div className="rounded-sm border border-primary/30 bg-primary-50/50 p-2 text-xs">
        <div className="micro-label mb-1 text-primary">Regulatory rule</div>
        {violation.regulator_quote && (
          <p className="leading-snug italic">&ldquo;{violation.regulator_quote}&rdquo;</p>
        )}
        {violation.rule_id && (
          <p className="mt-1 font-mono text-[11px] text-muted-foreground">
            Rule: {violation.rule_id}
          </p>
        )}
      </div>
    );
  }

  if (grounding === "novel" && regulatoryBasis) {
    return (
      <div className="rounded-sm border border-border bg-surface p-2 text-xs">
        <div className="micro-label mb-1">Novel finding &mdash; regulatory basis</div>
        <p className="leading-snug">{regulatoryBasis}</p>
      </div>
    );
  }

  return null;
}
