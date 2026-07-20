"use client";

import { Badge } from "@/components/ui/badge";
import { StatusPill } from "@/components/ui/status-pill";
import type { Violation } from "@/lib/types";

/**
 * Sub-confidence-floor findings (`suppressed:true`) — routed to manual review
 * only and excluded from the score (recall fix, 2026-06-08). Rendered as a
 * visually distinct lane, never mixed into the scored violation list.
 */
export function NeedsReviewLane({ violations }: { violations: Violation[] }) {
  if (violations.length === 0) return null;

  return (
    <div className="shrink-0 border-t-2 border-dashed border-warning/40 bg-warning/5">
      <div className="flex items-center gap-2 px-4 pt-4">
        <span className="micro-label text-warning">Needs review — not scored</span>
        <Badge variant="warning">{violations.length}</Badge>
      </div>
      <p className="px-4 pb-2 pt-1 text-xs text-muted-foreground">
        Below the confidence floor for automatic scoring. Confirm manually before acting on these.
      </p>
      <div className="max-h-64 space-y-2 overflow-y-auto p-4 pt-0">
        {violations.map((v) => (
          <div key={v.id} className="rounded-md border border-border bg-card p-3">
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill severity={v.severity}>{v.severity}</StatusPill>
              <span className="text-xs font-medium capitalize text-muted-foreground">{v.category}</span>
              {v.confidence != null && (
                <span className="ml-auto font-mono text-[11px] text-muted-foreground">
                  {Math.round(v.confidence * 100)}% confidence
                </span>
              )}
            </div>
            <p className="mt-2 text-sm text-foreground">{v.description}</p>
            {v.suppressed_reason && (
              <p className="mt-1 text-xs italic text-muted-foreground">{v.suppressed_reason}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
