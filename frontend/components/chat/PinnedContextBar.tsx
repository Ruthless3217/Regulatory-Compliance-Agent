"use client";
import { categoryLabel } from "@/lib/format";
import type { Submission, Violation } from "@/lib/types";

interface Props {
  submission: Submission;
  violations: Violation[];
  selectedViolationId: string | null;
}

export function PinnedContextBar({ submission, violations, selectedViolationId }: Props) {
  const cats = Array.from(new Set(violations.map((v) => v.category)));
  const selected = violations.find((v) => v.id === selectedViolationId);

  return (
    <div className="flex items-center justify-between gap-3 border border-border bg-primary-50 px-4 py-2 rounded-md">
      <div className="flex min-w-0 items-center gap-2 text-sm">
        <span className="micro-label text-primary">Discussing</span>
        <span className="truncate font-medium">{submission.title}</span>
        <span className="text-muted-foreground">·</span>
        <span className="font-mono text-xs text-muted-foreground">{violations.length} violations</span>
        {cats.length > 0 && (
          <>
            <span className="text-muted-foreground">·</span>
            <span className="text-xs text-muted-foreground">
              {cats.map(categoryLabel).join(" / ")}
            </span>
          </>
        )}
      </div>
      {selected && (
        <span className="rounded-sm border border-primary bg-background px-2 py-0.5 text-[10px] uppercase tracking-micro text-primary">
          Focused on violation #{selected.id.slice(0, 6)}
        </span>
      )}
    </div>
  );
}
