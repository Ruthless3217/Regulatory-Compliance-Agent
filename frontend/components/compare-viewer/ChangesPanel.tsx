"use client";
import * as React from "react";
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from "lucide-react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { useViewer } from "./ViewerContext";
import { ChangeCard } from "./ChangeCard";
import { FiltersPopover } from "./FiltersPopover";

export function ChangesPanel() {
  const { allChanges, visibleChanges, selectedChangeId, selectChange, stepChange, annotationFor } = useViewer();

  const scrollParentRef = React.useRef<HTMLDivElement>(null);
  const virtualizer = useVirtualizer({
    count: visibleChanges.length,
    getScrollElement: () => scrollParentRef.current,
    estimateSize: () => 92,
    overscan: 8,
  });

  // Keep the selected card in view (selection may come from either pane or the map).
  React.useEffect(() => {
    if (!selectedChangeId) return;
    const idx = visibleChanges.findIndex((c) => c.id === selectedChangeId);
    if (idx >= 0) virtualizer.scrollToIndex(idx, { align: "center" });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedChangeId]);

  const selectedPos = visibleChanges.findIndex((c) => c.id === selectedChangeId);
  const navBtn = "rounded-sm border border-border p-0.5 text-muted-foreground hover:text-foreground disabled:opacity-40";

  return (
    <aside className="flex min-h-0 flex-col border-l border-border bg-background" data-testid="changes-panel">
      <div className="flex items-center justify-between gap-3 border-b border-border bg-muted/30 px-3 py-2">
        <span className="text-[13px] font-medium text-[#1E579B] dark:text-sky-300">Changes</span>
        <FiltersPopover />
      </div>

      <div ref={scrollParentRef} className="min-h-0 flex-1 overflow-y-auto p-2">
        {visibleChanges.length === 0 ? (
          <div className="px-3 py-10 text-center text-sm text-muted-foreground">
            {allChanges.length === 0 ? "No changes — the documents are identical." : "No changes match the current filters."}
          </div>
        ) : (
          <div style={{ height: virtualizer.getTotalSize(), position: "relative", width: "100%" }}>
            {virtualizer.getVirtualItems().map((vi) => {
              const c = visibleChanges[vi.index];
              return (
                <div
                  key={c.id}
                  ref={virtualizer.measureElement}
                  data-index={vi.index}
                  style={{ position: "absolute", top: 0, left: 0, width: "100%", transform: `translateY(${vi.start}px)` }}
                  className="p-0.5"
                >
                  <ChangeCard
                    change={c}
                    selected={selectedChangeId === c.id}
                    annotation={annotationFor(c.id)}
                    onSelect={() => selectChange(c.id, "list")}
                  />
                </div>
              );
            })}
          </div>
        )}
      </div>

      <div className="flex items-center justify-between border-t border-border bg-muted/20 px-3 py-2 text-[11px]">
        <span className="text-muted-foreground" data-testid="change-counter">
          {selectedPos >= 0 ? (
            <>
              Change <span className="font-mono text-foreground">{selectedPos + 1}</span> of{" "}
              <span className="font-mono text-foreground">{visibleChanges.length}</span>
            </>
          ) : (
            <>
              <span className="font-mono text-foreground">{visibleChanges.length}</span> changes
            </>
          )}
        </span>
        <span className="flex items-center gap-1">
          <button type="button" onClick={() => stepChange("first")} disabled={!visibleChanges.length} className={navBtn} title="Jump to first change" aria-label="Jump to first change">
            <ChevronsLeft className="h-3.5 w-3.5" />
          </button>
          <button type="button" onClick={() => stepChange(-1)} disabled={!visibleChanges.length} className={navBtn} title="Previous change" aria-label="Previous change">
            <ChevronLeft className="h-3.5 w-3.5" />
          </button>
          <button type="button" onClick={() => stepChange(1)} disabled={!visibleChanges.length} className={navBtn} title="Next change" aria-label="Next change">
            <ChevronRight className="h-3.5 w-3.5" />
          </button>
          <button type="button" onClick={() => stepChange("last")} disabled={!visibleChanges.length} className={navBtn} title="Jump to last change" aria-label="Jump to last change">
            <ChevronsRight className="h-3.5 w-3.5" />
          </button>
        </span>
      </div>
    </aside>
  );
}
