// frontend/components/compare/ChangesPane.tsx
"use client";
import * as React from "react";
import { cn } from "@/lib/utils";
import { pluralizeWords } from "@/lib/format";
import type { ChangeItem, ChangeKind } from "@/lib/types";

interface Props {
  changes: ChangeItem[];
  removed: number;
  added: number;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

type FilterKey = "all" | ChangeKind;

const KIND_LABEL: Record<ChangeKind, string> = {
  removed: "Removed",
  added: "Added",
  modified: "Modified",
  moved: "Moved",
};

export function ChangesPane({ changes, removed, added, selectedId, onSelect }: Props) {
  const [filter, setFilter] = React.useState<FilterKey>("all");
  const refs = React.useRef<Record<string, HTMLButtonElement | null>>({});

  const counts = React.useMemo(() => {
    const base: Record<FilterKey, number> = {
      all: changes.length,
      removed: 0,
      added: 0,
      modified: 0,
      moved: 0,
    };
    for (const c of changes) base[c.kind] += 1;
    return base;
  }, [changes]);

  const filtered = React.useMemo(
    () => (filter === "all" ? changes : changes.filter((c) => c.kind === filter)),
    [filter, changes]
  );

  // Scroll the selected card into view when selection changes (mirrors ViolationsPane).
  React.useEffect(() => {
    if (!selectedId) return;
    refs.current[selectedId]?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [selectedId]);

  const chips: { key: FilterKey; label: string }[] = [
    { key: "all", label: "All" },
    { key: "removed", label: "Removed" },
    { key: "added", label: "Added" },
    { key: "modified", label: "Modified" },
  ];

  return (
    <aside className="flex max-h-[calc(70vh+2.5rem)] flex-col overflow-hidden rounded-lg border border-border bg-background shadow-card">
      {/* Header: word tallies */}
      <div className="flex items-center justify-between gap-3 border-b border-border bg-muted/30 px-4 py-2.5">
        <span className="micro-label">Changes</span>
        <span className="flex items-center gap-3 text-[11px]">
          <span className="font-mono text-sev-critical">− {pluralizeWords(removed)}</span>
          <span className="font-mono text-success">+ {pluralizeWords(added)}</span>
        </span>
      </div>

      {/* Filter chips */}
      <div className="flex flex-wrap gap-1.5 border-b border-border px-3 py-2">
        {chips.map((c) => {
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

      {/* List */}
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {filtered.length === 0 ? (
          <div className="px-3 py-10 text-center text-sm text-muted-foreground">
            {changes.length === 0 ? "No changes — the documents are identical." : "No changes in this filter."}
          </div>
        ) : (
          <ul className="space-y-1.5">
            {filtered.map((c) => (
              <li key={c.id}>
                <ChangeCard
                  change={c}
                  selected={selectedId === c.id}
                  onSelect={() => onSelect(c.id)}
                  ref={(el) => { refs.current[c.id] = el; }}
                />
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}

const ChangeCard = React.forwardRef<
  HTMLButtonElement,
  { change: ChangeItem; selected: boolean; onSelect: () => void }
>(function ChangeCard({ change, selected, onSelect }, ref) {
  const toneDot =
    change.kind === "removed"
      ? "bg-sev-critical/40"
      : change.kind === "added"
      ? "bg-success/40"
      : "bg-primary/40";

  return (
    <button
      ref={ref}
      type="button"
      onClick={onSelect}
      className={cn(
        "block w-full rounded-md border px-3 py-2 text-left transition-colors",
        selected
          ? "border-primary/50 bg-primary-50"
          : "border-border bg-background hover:border-foreground/40 hover:bg-muted/40"
      )}
    >
      <div className="mb-1 flex items-center gap-1.5">
        <span className={cn("inline-block h-1.5 w-1.5 rounded-full", toneDot)} />
        <span className="micro-label">{KIND_LABEL[change.kind]}</span>
      </div>
      {change.removedText ? (
        <p className="line-clamp-2 text-[12px] leading-snug text-sev-critical line-through">
          {change.removedText}
        </p>
      ) : null}
      {change.addedText ? (
        <p className="line-clamp-2 text-[12px] leading-snug text-success">
          {change.addedText}
        </p>
      ) : null}
    </button>
  );
});
