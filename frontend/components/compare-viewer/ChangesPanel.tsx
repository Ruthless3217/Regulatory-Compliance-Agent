"use client";
import * as React from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { cn } from "@/lib/utils";
import { countDiffStats, pluralizeWords } from "@/lib/format";
import { useViewer, type ViewerFilter, type ViewerChange } from "./ViewerContext";
import { ChangeCard } from "./ChangeCard";

const CHIPS: { key: ViewerFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "removed", label: "Removed" },
  { key: "added", label: "Added" },
  { key: "modified", label: "Modified" },
  { key: "moved", label: "Moved" },
  { key: "noted", label: "Noted" },
];

export function ChangesPanel() {
  const {
    comparison,
    filter,
    setFilter,
    selectedChangeId,
    setSelectedChangeId,
    annotationFor,
    viewerChanges: changes,
  } = useViewer();

  const isNoted = React.useCallback(
    (c: ViewerChange) => {
      const a = annotationFor(c.id);
      return !!a && (!!a.note?.trim() || a.tags.length > 0);
    },
    [annotationFor]
  );

  const counts = React.useMemo(() => {
    const base: Record<ViewerFilter, number> = {
      all: changes.length,
      removed: 0,
      added: 0,
      modified: 0,
      moved: 0,
      noted: 0,
    };
    for (const c of changes) {
      base[c.kind] += 1;
      if (isNoted(c)) base.noted += 1;
    }
    return base;
  }, [changes, isNoted]);

  const filtered = React.useMemo(() => {
    if (filter === "all") return changes;
    if (filter === "noted") return changes.filter(isNoted);
    return changes.filter((c) => c.kind === filter);
  }, [filter, changes, isNoted]);

  const { removed, added } = React.useMemo(
    () => countDiffStats(comparison.diff_result ?? []),
    [comparison.diff_result]
  );

  const scrollParentRef = React.useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => scrollParentRef.current,
    estimateSize: () => 96,
    overscan: 8,
  });

  // Keep the selected card in view.
  React.useEffect(() => {
    if (!selectedChangeId) return;
    const idx = filtered.findIndex((c) => c.id === selectedChangeId);
    if (idx >= 0) virtualizer.scrollToIndex(idx, { align: "center" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedChangeId]);

  const selectedPos = filtered.findIndex((c) => c.id === selectedChangeId);
  const step = (dir: 1 | -1) => {
    if (filtered.length === 0) return;
    const base = selectedPos < 0 ? (dir === 1 ? -1 : 0) : selectedPos;
    const next = (base + dir + filtered.length) % filtered.length;
    setSelectedChangeId(filtered[next].id);
  };

  return (
    <aside className="flex min-h-0 flex-col border-l border-border bg-background">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 border-b border-border bg-muted/30 px-4 py-2.5">
        <span className="micro-label">Changes</span>
        <span className="flex items-center gap-3 text-[11px]">
          <span className="font-mono text-sev-critical">− {pluralizeWords(removed)}</span>
          <span className="font-mono text-success">+ {pluralizeWords(added)}</span>
        </span>
      </div>

      {/* Filter chips */}
      <div className="flex flex-wrap gap-1.5 border-b border-border px-3 py-2">
        {CHIPS.map((c) => {
          const active = filter === c.key;
          return (
            <button
              key={c.key}
              type="button"
              onClick={() => setFilter(c.key)}
              className={cn(
                "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 text-[11px] transition-colors",
                active
                  ? "border-foreground bg-foreground text-background"
                  : "border-border bg-background text-muted-foreground hover:border-foreground hover:text-foreground"
              )}
            >
              {c.label}
              <span className="font-mono opacity-70">{counts[c.key]}</span>
            </button>
          );
        })}
      </div>

      {/* Virtualized list */}
      <div ref={scrollParentRef} className="min-h-0 flex-1 overflow-y-auto p-2">
        {filtered.length === 0 ? (
          <div className="px-3 py-10 text-center text-sm text-muted-foreground">
            {changes.length === 0
              ? "No changes — the documents are identical."
              : "No changes in this filter."}
          </div>
        ) : (
          <div style={{ height: virtualizer.getTotalSize(), position: "relative", width: "100%" }}>
            {virtualizer.getVirtualItems().map((vi) => {
              const c = filtered[vi.index];
              return (
                <div
                  key={c.id}
                  ref={virtualizer.measureElement}
                  data-index={vi.index}
                  style={{
                    position: "absolute",
                    top: 0,
                    left: 0,
                    width: "100%",
                    transform: `translateY(${vi.start}px)`,
                  }}
                  className="p-0.5"
                >
                  <ChangeCard
                    change={c}
                    index={vi.index + 1}
                    selected={selectedChangeId === c.id}
                    annotation={annotationFor(c.id)}
                    onSelect={() => setSelectedChangeId(c.id)}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* Footer: Change N of M */}
      <div className="flex items-center justify-between border-t border-border bg-muted/20 px-3 py-2 text-[11px]">
        <span className="text-muted-foreground">
          {selectedPos >= 0 ? (
            <>
              Change <span className="font-mono text-foreground">{selectedPos + 1}</span> of{" "}
              <span className="font-mono text-foreground">{filtered.length}</span>
            </>
          ) : (
            <>
              <span className="font-mono text-foreground">{filtered.length}</span> changes
            </>
          )}
        </span>
        <span className="flex items-center gap-1">
          <button
            type="button"
            onClick={() => step(-1)}
            disabled={filtered.length === 0}
            className="rounded-sm border border-border p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-40"
            title="Previous change"
          >
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <button
            type="button"
            onClick={() => step(1)}
            disabled={filtered.length === 0}
            className="rounded-sm border border-border p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-40"
            title="Next change"
          >
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
        </span>
      </div>
    </aside>
  );
}
