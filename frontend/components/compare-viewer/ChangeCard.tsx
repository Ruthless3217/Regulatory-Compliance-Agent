"use client";
import * as React from "react";
import { toast } from "sonner";
import type { Annotation } from "@/lib/types";
import { upsertAnnotation, deleteAnnotation } from "@/lib/api";
import { cn } from "@/lib/utils";
import { useViewer, markupFor, type ViewerChange } from "./ViewerContext";
import { SemanticBadge } from "./SemanticBadge";

const KIND_LABEL: Record<string, string> = { added: "Inserted", removed: "Deleted", modified: "Replaced", moved: "Moved" };
const GROUP_NOTE: Record<string, string> = {
  case: "Case only", punctuation: "Punctuation only", quotes: "Quote style", spacing: "Spacing only",
  currency: "Currency symbol", placeholders: "Placeholder / blank", renumbering: "Renumbering", layout: "Repeated header",
  headers_footers: "Header / footer",
};

const PRESET_TAGS = ["critical", "review", "approved", "question"];

const wordCount = (s?: string) => (s && s.trim() ? s.trim().split(/\s+/).length : 0);

export function ChangeCard({
  change,
  selected,
  annotation,
  onSelect,
}: {
  change: ViewerChange;
  selected: boolean;
  annotation?: Annotation;
  onSelect: () => void;
}) {
  const { comparison, setAnnotationLocal, removeAnnotationLocal, markup, showMoves, hover } = useViewer();

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
  const moved = change.kind === "moved" && showMoves;
  const del = markupFor({ kind: moved ? "moved" : "removed", group: change.group }, markup, showMoves);
  const ins = markupFor({ kind: moved ? "moved" : "added", group: change.group }, markup, showMoves);
  const oldPage = change.old && !change.old.anchor ? change.old.page : undefined;
  const newPage = change.new && !change.new.anchor ? change.new.page : undefined;
  const pageLabel =
    oldPage !== undefined && newPage !== undefined
      ? oldPage === newPage ? `p. ${oldPage}` : `p. ${oldPage} → ${newPage}`
      : oldPage !== undefined ? `p. ${oldPage}` : newPage !== undefined ? `p. ${newPage}` : undefined;
  const groupNote = GROUP_NOTE[change.group];
  const totalLocations = Math.max(change.old?.locations?.length ?? 0, change.new?.locations?.length ?? 0);
  const sectionTitle = change.structure?.title;

  return (
    <div
      data-testid="change-card"
      data-change-id={change.id}
      data-selected={selected || undefined}
      // Draftable: hovering a card outlines its change on both documents
      onMouseEnter={() => hover.set({ kind: "change", id: change.id })}
      onMouseLeave={() => hover.clear(change.id)}
      className={cn(
        "rounded-md border bg-background transition-colors",
        selected ? "border-[#00B2FF] ring-1 ring-[#00B2FF]" : "border-border hover:border-foreground/40"
      )}
    >
      <button type="button" onClick={onSelect} className="block w-full px-3 py-2 text-left">
        <div className="mb-1.5 flex flex-wrap items-center gap-1.5">
          <span className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {change.order}. {KIND_LABEL[change.kind] ?? change.kind}
          </span>
          {groupNote && (
            <span className="rounded bg-muted px-1 py-0.5 text-[9px] uppercase tracking-wide text-muted-foreground">{groupNote}</span>
          )}
          {change.category === "content" || !change.category ? (
            <SemanticBadge changeType={change.changeType} kind={change.kind} />
          ) : null}
          {pageLabel && (
            <span className="rounded bg-muted/60 px-1 py-0.5 font-mono text-[9px] text-muted-foreground">{pageLabel}</span>
          )}
          {totalLocations > 1 && (
            <span className="rounded bg-muted/80 px-1 py-0.5 font-mono text-[9px] text-muted-foreground" title={`Spans ${totalLocations} rendered boxes`}>
              {totalLocations} locs
            </span>
          )}
          <span className="ml-auto flex items-center gap-1.5 font-mono text-[11px]">
            {nRemoved > 0 && <span style={{ color: del.text }}>−{nRemoved}</span>}
            {nRemoved > 0 && nAdded > 0 && <span className="text-border">|</span>}
            {nAdded > 0 && <span style={{ color: ins.text }}>+{nAdded}</span>}
          </span>
        </div>

        {sectionTitle && (
          <div className="mb-1 truncate text-[10px] font-medium text-muted-foreground" title={sectionTitle}>
            § {sectionTitle}
          </div>
        )}

        {change.removedText ? (
          <p className="mb-1 line-clamp-3 text-[12px] leading-snug">
            <span className="rounded-[2px] px-0.5 box-decoration-clone text-[#212121]" style={{ background: del.fill }}>
              {change.removedText}
            </span>
          </p>
        ) : null}
        {change.addedText ? (
          <p className="line-clamp-3 text-[12px] leading-snug">
            <span className="rounded-[2px] px-0.5 box-decoration-clone text-[#212121]" style={{ background: ins.fill }}>
              {change.addedText}
            </span>
          </p>
        ) : null}

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
