"use client";
import * as React from "react";
import { toast } from "sonner";
import type { Annotation } from "@/lib/types";
import { upsertAnnotation, deleteAnnotation } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useViewer, type ViewerChange } from "./ViewerContext";
import { SemanticBadge } from "./SemanticBadge";

const PRESET_TAGS = ["critical", "review", "approved", "question"];

const wordCount = (s?: string) => (s && s.trim() ? s.trim().split(/\s+/).length : 0);

export function ChangeCard({
  change,
  index,
  selected,
  annotation,
  onSelect,
}: {
  change: ViewerChange;
  index: number;
  selected: boolean;
  annotation?: Annotation;
  onSelect: () => void;
}) {
  const { comparison, setAnnotationLocal, removeAnnotationLocal } = useViewer();

  const [note, setNote] = React.useState(annotation?.note ?? "");
  const [tags, setTags] = React.useState<string[]>(annotation?.tags ?? []);
  const debounceRef = React.useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  // Reset local editor state when this card is reused for a different change.
  React.useEffect(() => {
    setNote(annotation?.note ?? "");
    setTags(annotation?.tags ?? []);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [change.id]);

  const persist = (nextNote: string, nextTags: string[]) => {
    const prev = annotation;
    clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(async () => {
      const hasContent = !!nextNote.trim() || nextTags.length > 0;
      if (!hasContent) {
        removeAnnotationLocal(change.id);
        try {
          await deleteAnnotation(comparison.id, change.id);
        } catch {
          if (prev) setAnnotationLocal(prev);
          toast.error("Couldn't remove the note");
        }
        return;
      }
      const optimistic: Annotation = {
        change_id: change.id,
        note: nextNote.trim() || null,
        tags: nextTags,
        updated_at: new Date().toISOString(),
      };
      setAnnotationLocal(optimistic);
      try {
        const saved = await upsertAnnotation(comparison.id, {
          change_id: change.id,
          note: nextNote.trim() || null,
          tags: nextTags,
        });
        setAnnotationLocal(saved);
      } catch {
        if (prev) setAnnotationLocal(prev);
        else removeAnnotationLocal(change.id);
        toast.error("Couldn't save the note");
      }
    }, 600);
  };

  const onNoteChange = (v: string) => {
    setNote(v);
    persist(v, tags);
  };
  const toggleTag = (tag: string) => {
    const nextTags = tags.includes(tag) ? tags.filter((t) => t !== tag) : [...tags, tag];
    setTags(nextTags);
    persist(note, nextTags);
  };

  const nRemoved = wordCount(change.removedText);
  const nAdded = wordCount(change.addedText);
  const isReordered = change.changeType === "reordered" || change.kind === "moved";

  const nOldLocs = change.oldLocations?.length ?? (change.removedText ? 1 : 0);
  const nNewLocs = change.newLocations?.length ?? (change.addedText ? 1 : 0);
  const totalLocations = Math.max(nOldLocs, nNewLocs);

  const sectionTitle = change.structure?.title;

  return (
    <div
      className={cn(
        "rounded-md border transition-colors",
        selected
          ? "border-primary/50 bg-primary-50 dark:bg-primary-950/20"
          : "border-border bg-background hover:border-foreground/40 hover:bg-muted/40"
      )}
    >
      <button type="button" onClick={onSelect} className="block w-full px-3 py-2 text-left">
        {/* Header row: Semantic Badge, #Index, Page, Multi-location, Word Delta */}
        <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
          <SemanticBadge changeType={change.changeType} kind={change.kind} />
          <span className="font-mono text-[10px] text-muted-foreground">#{index}</span>

          {/* Page Badge */}
          {change.oldPage !== undefined || change.newPage !== undefined || change.page !== undefined ? (
            <span className="rounded bg-muted/60 px-1 py-0.5 font-mono text-[9px] text-muted-foreground">
              {change.oldPage !== undefined && change.newPage !== undefined
                ? change.oldPage === change.newPage
                  ? `p. ${change.oldPage}`
                  : `p. ${change.oldPage} → ${change.newPage}`
                : change.oldPage !== undefined
                ? `p. ${change.oldPage}`
                : change.newPage !== undefined
                ? `p. ${change.newPage}`
                : `p. ${change.page}`}
            </span>
          ) : null}

          {/* Multi-location indicator */}
          {totalLocations > 1 && (
            <span
              className="rounded bg-muted/80 px-1 py-0.5 font-mono text-[9px] text-muted-foreground"
              title={`Spans ${totalLocations} rendered boxes`}
            >
              {totalLocations} locs
            </span>
          )}

          {/* Word count delta */}
          <span className="ml-auto flex items-center gap-1.5 font-mono text-[10px]">
            {nRemoved > 0 && <span className="text-sev-critical">−{nRemoved}</span>}
            {nAdded > 0 && <span className="text-success">+{nAdded}</span>}
          </span>
        </div>

        {/* Structural Section Header (if present) */}
        {sectionTitle && (
          <div className="mb-1 truncate text-[10px] font-medium text-muted-foreground" title={sectionTitle}>
            § {sectionTitle}
          </div>
        )}

        {/* Value / Text Diff */}
        {change.removedText ? (
          <p
            className={cn(
              "line-clamp-2 text-[12px] leading-snug",
              isReordered ? "text-violet-700 dark:text-violet-300" : "text-sev-critical line-through"
            )}
          >
            {change.removedText}
          </p>
        ) : null}
        {change.addedText ? (
          <p
            className={cn(
              "line-clamp-2 text-[12px] leading-snug",
              isReordered ? "text-violet-700 dark:text-violet-300" : "text-success"
            )}
          >
            {change.addedText}
          </p>
        ) : null}

        {/* Annotations & Tags Preview */}
        {annotation && (annotation.note?.trim() || annotation.tags.length > 0) && !selected ? (
          <div className="mt-1 flex flex-wrap items-center gap-1">
            {annotation.tags.map((t) => (
              <span key={t} className="rounded-sm bg-muted px-1 text-[9px] uppercase tracking-wide text-muted-foreground">
                {t}
              </span>
            ))}
            {annotation.note?.trim() ? <span className="text-[10px] text-muted-foreground">✎</span> : null}
          </div>
        ) : null}
      </button>

      {/* Selected Card Note Editor */}
      {selected && (
        <div className="border-t border-border/60 px-3 py-2">
          <div className="mb-2 flex flex-wrap gap-1">
            {PRESET_TAGS.map((tag) => {
              const on = tags.includes(tag);
              return (
                <button
                  key={tag}
                  type="button"
                  onClick={() => toggleTag(tag)}
                  className={cn(
                    "rounded-sm border px-1.5 py-0.5 text-[10px] uppercase tracking-wide transition-colors",
                    on
                      ? "border-primary bg-primary text-primary-foreground"
                      : "border-border bg-background text-muted-foreground hover:border-foreground hover:text-foreground"
                  )}
                >
                  {tag}
                </button>
              );
            })}
          </div>
          <textarea
            value={note}
            onChange={(e) => onNoteChange(e.target.value)}
            placeholder="Add a note…"
            rows={2}
            className="w-full resize-y rounded-sm border border-border bg-background px-2 py-1 text-[12px] placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
          />
        </div>
      )}
    </div>
  );
}
