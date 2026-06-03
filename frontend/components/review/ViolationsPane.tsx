"use client";
import * as React from "react";
import { FilterChipBar, type FilterKey } from "./FilterChipBar";
import { ViolationCard } from "./ViolationCard";
import { severityOrder } from "@/lib/format";
import type { Violation } from "@/lib/types";

interface Props {
  violations: Violation[];
  selectedViolationId: string | null;
  setSelectedViolationId: (id: string | null) => void;
}

export function ViolationsPane({ violations, selectedViolationId, setSelectedViolationId }: Props) {
  const [filter, setFilter] = React.useState<FilterKey>("all");
  const [dismissed, setDismissed] = React.useState<Set<string>>(new Set());
  const refs = React.useRef<Record<string, HTMLDivElement | null>>({});

  const sorted = React.useMemo(
    () => [...violations].sort((a, b) => severityOrder(a.severity) - severityOrder(b.severity)),
    [violations]
  );

  const counts = React.useMemo(() => {
    const base: Record<FilterKey, number> = { all: sorted.length, critical: 0, high: 0, medium: 0, low: 0 };
    for (const v of sorted) {
      const s = v.severity.toLowerCase() as FilterKey;
      if (s in base && s !== "all") base[s] += 1;
    }
    return base;
  }, [sorted]);

  const filtered = React.useMemo(
    () => (filter === "all" ? sorted : sorted.filter((v) => v.severity.toLowerCase() === filter)),
    [filter, sorted]
  );

  // Scroll selected card into view when selection changes
  React.useEffect(() => {
    if (!selectedViolationId) return;
    const el = refs.current[selectedViolationId];
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [selectedViolationId]);

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col border-l border-border bg-background">
      <FilterChipBar counts={counts} value={filter} onChange={setFilter} />
      <div className="min-h-0 flex-1 overflow-y-auto">
        <div className="space-y-3 p-4">
          {filtered.length === 0 ? (
            <div className="rounded-md border border-border bg-background p-8 text-center text-sm text-muted-foreground">
              No violations in this filter.
            </div>
          ) : (
            filtered.map((v, i) => (
              <ViolationCard
                key={v.id}
                index={i}
                violation={v}
                selected={v.id === selectedViolationId}
                dismissed={dismissed.has(v.id)}
                onSelect={() => setSelectedViolationId(v.id)}
                onDismiss={() => setDismissed((d) => new Set(d).add(v.id))}
                ref={(el) => { refs.current[v.id] = el; }}
              />
            ))
          )}
        </div>
      </div>
    </div>
  );
}
