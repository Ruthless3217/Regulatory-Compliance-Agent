"use client";
import * as React from "react";
import type { Violation } from "@/lib/types";

/**
 * Shows the provenance behind a violation:
 *  - precedent-grounded → the historical reviewer's actual comment on a matching
 *    past document, the phrase it was flagged on, and the source ticket.
 *  - novel             → the regulatory basis (no historical match to point at).
 *
 * Used on both the Review tab (ViolationCard) and the report (ViolationGroup).
 */
export function PrecedentNote({ violation }: { violation: Violation }) {
  const meta = violation.violation_metadata ?? null;
  const isNovel = meta?.grounding === "novel";
  const regulatoryBasis = meta?.regulatory_basis;

  if (!isNovel && violation.cited_comment_verbatim) {
    return (
      <div className="rounded-sm border border-primary/30 bg-primary-50/50 p-2 text-xs">
        <div className="micro-label mb-1 text-primary">Compliance reviewer note</div>
        <p className="leading-snug">{violation.cited_comment_verbatim}</p>
        {violation.cited_anchor_text && (
          <p className="mt-1.5 text-[11px] text-muted-foreground">
            Flagged on: “{violation.cited_anchor_text}”
          </p>
        )}
        {violation.cited_document_id && (
          <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
            Precedent: {violation.cited_document_id}
          </p>
        )}
      </div>
    );
  }

  if (isNovel && regulatoryBasis) {
    return (
      <div className="rounded-sm border border-border bg-surface p-2 text-xs">
        <div className="micro-label mb-1">Novel finding — regulatory basis</div>
        <p className="leading-snug">{regulatoryBasis}</p>
      </div>
    );
  }

  return null;
}
