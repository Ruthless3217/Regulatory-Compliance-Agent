// frontend/components/compare/DiffViewer.tsx
"use client";
import * as React from "react";
import type { DiffBlock } from "@/lib/types";
import { cn } from "@/lib/utils";

export const diffRowDomId = (index: number) => `cmp-block-${index}`;

interface DiffViewerProps {
  blocks: DiffBlock[];
  /** blockIndex (as string) of the change currently selected in the sidebar */
  selectedId?: string | null;
  /** called with a changed block's id (its index as string) when its row is clicked */
  onSelect?: (id: string) => void;
  /**
   * Optional per-row className passthrough (compare-viewer uses it to draw a
   * violet "moved" left border). Backward-compatible: unused by the old pages.
   */
  rowClassName?: (block: DiffBlock, index: number) => string | undefined;
  /** Pane header labels; default to the generic Original/Revised. */
  oldLabel?: string;
  newLabel?: string;
}

export function DiffViewer({
  blocks,
  selectedId = null,
  onSelect,
  rowClassName,
  oldLabel = "Original",
  newLabel = "Revised",
}: DiffViewerProps) {
  const scrollRef = React.useRef<HTMLDivElement>(null);

  // Mirror the review page's DocumentPane: when the selection changes, scroll
  // the matching row into view and pulse it briefly.
  React.useEffect(() => {
    if (selectedId == null) return;
    const root = scrollRef.current;
    if (!root) return;
    const el = root.querySelector<HTMLElement>(`#${CSS.escape(diffRowDomId(Number(selectedId)))}`);
    if (!el) return;
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    el.dataset.pulse = "true";
    const t = setTimeout(() => { el.dataset.pulse = "false"; }, 850);
    return () => clearTimeout(t);
  }, [selectedId]);

  return (
    <div className="overflow-hidden rounded-lg border border-border bg-background shadow-card">
      <div className="grid grid-cols-2 border-b border-border bg-muted/30 text-xs">
        <div className="truncate border-r border-border px-4 py-2 micro-label" title={oldLabel}>{oldLabel}</div>
        <div className="truncate px-4 py-2 micro-label" title={newLabel}>{newLabel}</div>
      </div>
      <div ref={scrollRef} className="max-h-[70vh] overflow-y-auto">
        {blocks.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">
            No content to compare.
          </div>
        ) : (
          blocks.map((block, idx) => (
            <DiffRow
              key={idx}
              index={idx}
              block={block}
              selected={selectedId === String(idx)}
              onSelect={onSelect}
              extraClassName={rowClassName?.(block, idx)}
            />
          ))
        )}
      </div>
    </div>
  );
}

function DiffRow({
  index,
  block,
  selected,
  onSelect,
  extraClassName,
}: {
  index: number;
  block: DiffBlock;
  selected: boolean;
  onSelect?: (id: string) => void;
  extraClassName?: string;
}) {
  const isChange = block.type !== "equal";
  return (
    <div
      id={diffRowDomId(index)}
      onClick={isChange && onSelect ? () => onSelect(String(index)) : undefined}
      className={cn(
        "grid grid-cols-2 border-b border-border text-[13px] leading-relaxed last:border-0 scroll-mt-4 transition-colors",
        isChange && onSelect && "cursor-pointer hover:bg-muted/40",
        selected && "bg-primary-50",
        extraClassName
      )}
    >
      <div className="border-r border-border px-4 py-2">{renderOld(block)}</div>
      <div className="px-4 py-2">{renderNew(block)}</div>
    </div>
  );
}

function renderOld(block: DiffBlock) {
  if (block.type === "equal") return <span>{block.old_text}</span>;
  if (block.type === "insert") return null;
  if (block.type === "delete") {
    return (
      <span className="bg-sev-critical/10 text-sev-critical line-through">{block.old_text}</span>
    );
  }
  return (
    <span>
      {block.old_words.map((w, i) => (
        <span key={i} className={cn(w.changed && "bg-sev-critical/10 text-sev-critical line-through")}>
          {w.text}{" "}
        </span>
      ))}
    </span>
  );
}

function renderNew(block: DiffBlock) {
  if (block.type === "equal") return <span>{block.new_text}</span>;
  if (block.type === "delete") return null;
  if (block.type === "insert") {
    return <span className="bg-success/10 text-success">{block.new_text}</span>;
  }
  return (
    <span>
      {block.new_words.map((w, i) => (
        <span key={i} className={cn(w.changed && "bg-success/10 text-success")}>
          {w.text}{" "}
        </span>
      ))}
    </span>
  );
}
