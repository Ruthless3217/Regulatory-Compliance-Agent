"use client";
import * as React from "react";
import { FilterChipBar, type FilterKey } from "./FilterChipBar";
import { ViolationCard } from "./ViolationCard";
import { severityOrder, normalizeSeverity } from "@/lib/format";
import type { Violation } from "@/lib/types";

interface Props {
 violations: Violation[];
 selectedViolationId: string | null;
 setSelectedViolationId: (id: string | null) => void;
}

export function ViolationsPane({ violations, selectedViolationId, setSelectedViolationId }: Props) {
 const [filter, setFilter] = React.useState<FilterKey>("all");
 const [dismissed, setDismissed] = React.useState<Set<string>>(new Set());
 const [showSuppressed, setShowSuppressed] = React.useState(false);
 const refs = React.useRef<Record<string, HTMLDivElement | null>>({});

 // Suppressed (sub-confidence-floor / structural) findings are kept out of the
 // score and the severity filter — surfaced in a separate "Needs review" lane.
 const active = React.useMemo(() => violations.filter((v) => !v.suppressed), [violations]);
 const suppressed = React.useMemo(() => violations.filter((v) => v.suppressed), [violations]);

 const sorted = React.useMemo(
 () => [...active].sort((a, b) => severityOrder(a.severity) - severityOrder(b.severity)),
 [active]
 );

 const counts = React.useMemo(() => {
 const base: Record<FilterKey, number> = { all: sorted.length, critical: 0, high: 0, medium: 0, low: 0 };
 for (const v of sorted) base[normalizeSeverity(v.severity)] += 1;
 return base;
 }, [sorted]);

 const filtered = React.useMemo(
 () => (filter === "all" ? sorted : sorted.filter((v) => normalizeSeverity(v.severity) === filter)),
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

 {suppressed.length > 0 && (
 <div className="mt-2 border-t border-dashed border-border pt-3">
 <button
 type="button"
 onClick={() => setShowSuppressed((s) => !s)}
 className="flex w-full items-center justify-between text-left text-xs font-medium text-muted-foreground hover:text-foreground"
 >
 <span>
 Needs review ({suppressed.length}) — low-confidence or structural; not counted in the score
 </span>
 <span className="font-mono">{showSuppressed ? "−" : "+"}</span>
 </button>
 {showSuppressed && (
 <div className="mt-3 space-y-3 opacity-80">
 {suppressed.map((v, i) => (
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
 ))}
 </div>
 )}
 </div>
 )}
 </div>
 </div>
 </div>
 );
}
