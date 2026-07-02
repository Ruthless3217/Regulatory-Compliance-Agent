// frontend/components/compare/DiffViewer.tsx
"use client";
import type { DiffBlock } from "@/lib/types";
import { cn } from "@/lib/utils";

export function DiffViewer({ blocks }: { blocks: DiffBlock[] }) {
  return (
    <div className="overflow-hidden rounded-lg border border-border bg-background shadow-card">
      <div className="grid grid-cols-2 border-b border-border bg-muted/30 text-xs">
        <div className="border-r border-border px-4 py-2 micro-label">Original</div>
        <div className="px-4 py-2 micro-label">Revised</div>
      </div>
      <div className="max-h-[70vh] overflow-y-auto">
        {blocks.length === 0 ? (
          <div className="px-4 py-8 text-center text-sm text-muted-foreground">
            No content to compare.
          </div>
        ) : (
          blocks.map((block, idx) => <DiffRow key={idx} block={block} />)
        )}
      </div>
    </div>
  );
}

function DiffRow({ block }: { block: DiffBlock }) {
  return (
    <div className="grid grid-cols-2 border-b border-border text-[13px] leading-relaxed last:border-0">
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
