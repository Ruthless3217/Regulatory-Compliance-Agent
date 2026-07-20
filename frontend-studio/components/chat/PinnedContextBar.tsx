import { FileText } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { Submission } from "@/lib/types";

export function PinnedContextBar({ submission }: { submission: Submission | null }) {
  if (!submission) {
    return (
      <div className="flex items-center gap-3 border-b border-border bg-muted/40 px-6 py-3">
        <div className="h-4 w-40 animate-pulse rounded bg-muted" />
      </div>
    );
  }

  return (
    <div className="flex flex-wrap items-center gap-3 border-b border-border bg-muted/40 px-6 py-3">
      <FileText className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
      <span className="text-sm font-medium text-foreground">{submission.title}</span>
      <Badge variant="outline" className="font-mono text-[11px]">
        {submission.id}
      </Badge>
      <span className="text-xs text-muted-foreground">Pinned context for this conversation</span>
    </div>
  );
}
