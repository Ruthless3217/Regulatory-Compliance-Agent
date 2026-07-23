"use client";
import * as React from "react";
import type { RenderResult, RenderPage } from "@/lib/types";
import { comparisonPageImageUrl } from "@/lib/api";
import { cn } from "@/lib/utils";

export const pixelBoxDomId = (changeId: string, side: "old" | "new") => `pxl-${side}-${changeId}`;

interface Props {
  comparisonId: string;
  render: RenderResult;
  selectedId?: string | null;
  onSelect?: (id: string) => void;
  /** Pane header labels; default to the generic Original/Revised. */
  oldLabel?: string;
  newLabel?: string;
}

export function PixelDiffViewer({
  comparisonId,
  render,
  selectedId = null,
  onSelect,
  oldLabel = "Original",
  newLabel = "Revised",
}: Props) {
  const scrollRef = React.useRef<HTMLDivElement>(null);

  React.useEffect(() => {
    if (!selectedId || !scrollRef.current) return;
    const el =
      scrollRef.current.querySelector<HTMLElement>(`#${CSS.escape(pixelBoxDomId(selectedId, "old"))}`) ||
      scrollRef.current.querySelector<HTMLElement>(`#${CSS.escape(pixelBoxDomId(selectedId, "new"))}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [selectedId]);

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-background shadow-card">
      <div className="grid grid-cols-2 border-b border-border bg-muted/30 text-xs">
        <div className="truncate border-r border-border px-4 py-2 micro-label" title={oldLabel}>{oldLabel}</div>
        <div className="truncate px-4 py-2 micro-label" title={newLabel}>{newLabel}</div>
      </div>
      <div ref={scrollRef} className="grid max-h-[70vh] grid-cols-2 overflow-y-auto">
        <SideColumn comparisonId={comparisonId} side="old" pages={render.old.pages}
          selectedId={selectedId} onSelect={onSelect} />
        <SideColumn comparisonId={comparisonId} side="new" pages={render.new.pages}
          selectedId={selectedId} onSelect={onSelect} className="border-l border-border" />
      </div>
      {render.truncated_pages > 0 && (
        <div className="border-t border-border bg-warning/10 px-4 py-2 text-[11px] text-warning">
          {render.truncated_pages} page(s) not shown (render cap reached).
        </div>
      )}
    </div>
  );
}

function SideColumn({
  comparisonId, side, pages, selectedId, onSelect, className,
}: {
  comparisonId: string; side: "old" | "new"; pages: RenderPage[];
  selectedId?: string | null; onSelect?: (id: string) => void; className?: string;
}) {
  const boxColor = side === "old" ? "bg-sev-critical/25 ring-sev-critical/50" : "bg-success/25 ring-success/50";
  return (
    <div className={cn("space-y-3 p-3", className)}>
      {pages.map((p) => (
        <div key={p.n} className="relative w-full" style={{ aspectRatio: `${p.w_pt} / ${p.h_pt}` }}>
          <img
            src={comparisonPageImageUrl(comparisonId, side, p.n)}
            alt={`${side} page ${p.n}`}
            loading="lazy"
            className="block w-full border border-border"
          />
          {p.boxes.map((b, i) => (
            <button
              key={i}
              id={pixelBoxDomId(b.change_id, side)}
              type="button"
              onClick={onSelect ? () => onSelect(b.change_id) : undefined}
              className={cn(
                "absolute rounded-[1px] ring-1 transition-shadow",
                boxColor,
                selectedId === b.change_id && "ring-2 ring-primary"
              )}
              style={{
                left: `${(b.x0 / p.w_pt) * 100}%`,
                top: `${(b.y0 / p.h_pt) * 100}%`,
                width: `${((b.x1 - b.x0) / p.w_pt) * 100}%`,
                height: `${((b.y1 - b.y0) / p.h_pt) * 100}%`,
              }}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
