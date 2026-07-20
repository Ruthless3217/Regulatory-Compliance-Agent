"use client";

import { Card } from "@/components/ui/card";
import { cn } from "@/lib/utils";
import type { Severity, Violation } from "@/lib/types";

const SEVERITIES: Severity[] = ["critical", "high", "medium", "low"];
const SEV_TONE: Record<Severity, string> = {
  critical: "text-sev-critical",
  high: "text-sev-high",
  medium: "text-sev-medium",
  low: "text-sev-low",
};

const TIERS: { key: "precedent" | "rule" | "novel" | "product_fact"; label: string }[] = [
  { key: "precedent", label: "Precedent" },
  { key: "rule", label: "Rule" },
  { key: "novel", label: "Novel" },
  { key: "product_fact", label: "Product fact" },
];

/** Counts by severity and by grounding tier, over the violations that count
 * toward the score (suppressed sub-floor findings are called out separately
 * rather than folded into the headline tallies). */
export function KpiStrip({ violations }: { violations: Violation[] }) {
  const active = violations.filter((v) => !v.suppressed);
  const suppressedCount = violations.length - active.length;

  const severityCounts = SEVERITIES.map((s) => ({
    key: s,
    label: s[0].toUpperCase() + s.slice(1),
    count: active.filter((v) => v.severity === s).length,
  }));
  const tierCounts = TIERS.map((t) => ({
    ...t,
    count: active.filter((v) => v.violation_metadata?.grounding === t.key).length,
  }));

  return (
    <Card>
      <div className="grid grid-cols-1 divide-y divide-border sm:grid-cols-2 sm:divide-x sm:divide-y-0">
        <div className="p-5">
          <p className="micro-label mb-3">By severity</p>
          <div className="grid grid-cols-4 gap-4">
            {severityCounts.map((c) => (
              <div key={c.key}>
                <div className="text-xs text-muted-foreground">{c.label}</div>
                <div className={cn("font-mono text-2xl tabular-nums", SEV_TONE[c.key])}>{c.count}</div>
              </div>
            ))}
          </div>
          {suppressedCount > 0 && (
            <p className="mt-3 text-xs text-muted-foreground">
              +{suppressedCount} below the confidence floor — excluded from the score, routed to manual review.
            </p>
          )}
        </div>
        <div className="p-5">
          <p className="micro-label mb-3">By grounding tier</p>
          <div className="grid grid-cols-4 gap-4">
            {tierCounts.map((c) => (
              <div key={c.key}>
                <div className="text-xs text-muted-foreground">{c.label}</div>
                <div className="font-mono text-2xl tabular-nums">{c.count}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
}
