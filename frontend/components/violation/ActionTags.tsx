"use client";
import * as React from "react";
import { Badge } from "@/components/ui/badge";
import type { Violation, ActionType } from "@/lib/types";

// Action-type → badge tone. Mirrors the reviewer's mental model: rewrites are
// routine (primary), evidence requests block sign-off (high), removals are
// hard stops (critical).
const ACTION_TONE: Record<ActionType, "primary" | "high" | "medium" | "low" | "critical"> = {
  rewrite: "primary",
  "share-evidence": "high",
  "add-disclaimer": "medium",
  "verify-source": "low",
  remove: "critical",
};

/** Action / Needed / Source badge row derived from violation_metadata. */
export function ActionTags({ violation, className }: { violation: Violation; className?: string }) {
  const meta = violation.violation_metadata ?? null;
  const actionType = meta?.action_type as ActionType | undefined;
  const evidenceNeeded = meta?.evidence_needed;
  const isNovel = meta?.grounding === "novel";
  const regulatoryBasis = meta?.regulatory_basis;

  if (!actionType && !evidenceNeeded && !meta?.grounding) return null;

  return (
    <div className={className ?? "flex flex-wrap items-center gap-1.5"}>
      {actionType && <Badge tone={ACTION_TONE[actionType] ?? "default"}>Action: {actionType}</Badge>}
      {evidenceNeeded && <Badge tone="default">Needed: {evidenceNeeded}</Badge>}
      {isNovel ? (
        <Badge tone="default">Source: novel{regulatoryBasis ? ` · ${regulatoryBasis}` : ""}</Badge>
      ) : (
        meta?.grounding === "precedent" && <Badge tone="default">Source: precedent</Badge>
      )}
    </div>
  );
}
