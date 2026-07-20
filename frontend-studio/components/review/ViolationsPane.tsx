"use client";

import * as React from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { filterViolations, type ViolationGroup } from "@/lib/violationGroups";
import type { Violation } from "@/lib/types";
import { ViolationCard } from "./ViolationCard";
import { FilterChipBar, EMPTY_FILTER_STATE, type FilterState } from "./FilterChipBar";
import { NeedsReviewLane } from "./NeedsReviewLane";

export interface ViolationsPaneProps {
  groups: ViolationGroup[];
  suppressed: Violation[];
  selectedId?: string | null;
  onSelect?: (groupId: string) => void;
}

/** Filter bar + virtualized card list (one card per group) + the suppressed
 * "Needs review" lane, all scoped to a single scrollable pane. */
export function ViolationsPane({ groups, suppressed, selectedId, onSelect }: ViolationsPaneProps) {
  const [filters, setFilters] = React.useState<FilterState>(EMPTY_FILTER_STATE);
  const parentRef = React.useRef<HTMLDivElement>(null);

  const filteredGroups = React.useMemo(() => {
    const active = filters.severities.length > 0 || filters.categories.length > 0 || filters.tiers.length > 0;
    if (!active) return groups;
    return groups.filter((g) => filterViolations([g.primary], filters).length > 0);
  }, [groups, filters]);

  const virtualizer = useVirtualizer({
    count: filteredGroups.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 260,
    overscan: 4,
  });

  return (
    <div className="flex h-full flex-col">
      <FilterChipBar value={filters} onChange={setFilters} />
      <div ref={parentRef} className="min-h-0 flex-1 overflow-y-auto">
        {filteredGroups.length === 0 ? (
          <div className="flex h-40 items-center justify-center px-4 text-center text-sm text-muted-foreground">
            {groups.length === 0 ? "No violations found for this submission." : "No violations match the current filters."}
          </div>
        ) : (
          <div style={{ height: virtualizer.getTotalSize(), position: "relative" }}>
            {virtualizer.getVirtualItems().map((item) => {
              const group = filteredGroups[item.index];
              return (
                <div
                  key={group.id}
                  ref={virtualizer.measureElement}
                  data-index={item.index}
                  style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${item.start}px)` }}
                  className="px-4 py-2"
                >
                  <ViolationCard group={group} selected={selectedId === group.id} onSelect={onSelect} />
                </div>
              );
            })}
          </div>
        )}
      </div>
      <NeedsReviewLane violations={suppressed} />
    </div>
  );
}
