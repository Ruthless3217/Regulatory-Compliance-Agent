"use client";
import * as React from "react";
import { useRouter } from "next/navigation";
import { toast } from "sonner";
import { History, Eye, RotateCcw, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { listSubmissionRevisions, applySubmissionRevision } from "@/lib/api";
import { Popover } from "@/components/ui/popover";
import type { SubmissionRevision } from "@/lib/types";

const SOURCE_LABEL: Record<string, string> = {
  manual_edit: "Manual edit",
  apply_fix: "Applied fix",
  bulk_apply_fixes: "Bulk applied fixes",
  restore: "Restored",
};

interface Props {
  submissionId: string;
}

export function VersionHistoryPopover({ submissionId }: Props) {
  const router = useRouter();
  const [revisions, setRevisions] = React.useState<SubmissionRevision[] | null>(null);
  const [loading, setLoading] = React.useState(false);
  const [viewingId, setViewingId] = React.useState<string | null>(null);
  const [restoringId, setRestoringId] = React.useState<string | null>(null);

  const load = async () => {
    setLoading(true);
    try {
      const res = await listSubmissionRevisions(submissionId);
      setRevisions(res.revisions);
    } catch {
      toast.error("Could not load version history");
      setRevisions([]);
    } finally {
      setLoading(false);
    }
  };

  const restore = async (rev: SubmissionRevision) => {
    if (restoringId) return;
    setRestoringId(rev.id);
    try {
      await applySubmissionRevision(submissionId, {
        content: rev.content,
        source: "restore",
        note: `Restored from revision ${rev.revision_number}`,
      });
      toast.success(`Restored revision ${rev.revision_number}`);
      router.refresh();
      await load();
    } catch {
      toast.error("Could not restore this version");
    } finally {
      setRestoringId(null);
    }
  };

  const sorted = revisions ? [...revisions].sort((a, b) => b.revision_number - a.revision_number) : null;

  return (
    <Popover
      align="start"
      className="w-80 p-2"
      trigger={({ open, toggle }) => (
        <button
          type="button"
          onClick={() => {
            toggle();
            if (!revisions) load();
          }}
          className={cn(
            "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 text-[11px] transition-colors",
            open
              ? "border-foreground bg-foreground text-background"
              : "border-border text-muted-foreground hover:border-foreground hover:text-foreground"
          )}
        >
          <History className="h-3 w-3" />
          Version history
        </button>
      )}
    >
      <div className="max-h-96 space-y-0.5 overflow-y-auto">
        <div className="px-1.5 pb-1 micro-label">Revision history</div>
        {loading && (
          <div className="flex items-center gap-2 px-1.5 py-1.5 text-[12px] text-muted-foreground">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Loading…
          </div>
        )}
        {!loading && sorted?.length === 0 && (
          <div className="px-1.5 py-1.5 text-[12px] text-muted-foreground">
            No edits yet — this is the original upload.
          </div>
        )}
        {!loading &&
          sorted?.map((rev) => (
            <div key={rev.id} className="rounded-sm px-1.5 py-1.5 text-[12px] hover:bg-muted">
              <div className="flex items-center justify-between gap-2">
                <div className="min-w-0">
                  <div className="font-medium">
                    Revision {rev.revision_number} · {SOURCE_LABEL[rev.source] ?? rev.source}
                  </div>
                  <div className="truncate text-[10px] text-muted-foreground">
                    {rev.created_at ? new Date(rev.created_at).toLocaleString() : "—"}
                    {rev.note ? ` · ${rev.note}` : ""}
                  </div>
                </div>
                <div className="flex flex-shrink-0 items-center gap-1">
                  <button
                    type="button"
                    title="View this version"
                    onClick={() => setViewingId((id) => (id === rev.id ? null : rev.id))}
                    className="rounded-sm p-1 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
                  >
                    <Eye className="h-3.5 w-3.5" />
                  </button>
                  <button
                    type="button"
                    title="Restore this version"
                    disabled={restoringId === rev.id}
                    onClick={() => restore(rev)}
                    className="rounded-sm p-1 text-muted-foreground transition-colors hover:bg-background hover:text-foreground disabled:opacity-50"
                  >
                    {restoringId === rev.id ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <RotateCcw className="h-3.5 w-3.5" />
                    )}
                  </button>
                </div>
              </div>
              {viewingId === rev.id && (
                <div className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-sm border border-border bg-muted/20 p-2 text-[11px]">
                  {rev.content || "(empty)"}
                </div>
              )}
            </div>
          ))}
      </div>
    </Popover>
  );
}
