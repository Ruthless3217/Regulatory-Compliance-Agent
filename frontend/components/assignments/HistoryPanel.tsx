"use client";
import { useEffect, useState } from "react";
import { documentTrail } from "@/lib/api";
import type { TrailRow } from "@/lib/types";

/** Event type -> what a person would say happened. Anything unmapped falls
 *  back to the raw type, which is ugly but honest — better than hiding an
 *  event because nobody wrote a label for it yet. */
export const VERB: Record<string, string> = {
  assignment_created: "assigned for review",
  assignment_reassigned: "reassigned",
  assignment_started: "started review",
  assignment_completed: "completed review",
  assignment_sent_back: "sent back",
  assignment_closed: "closed the assignment",
  assignment_cancelled: "cancelled the assignment",
  submission_created: "uploaded",
  submission_edited: "edited",
  submission_deleted: "deleted",
  submission_approved: "approved",
  submission_approval_override: "approved with an override",
  comment_created: "commented",
  comment_updated: "edited a comment",
  comment_deleted: "deleted a comment",
  export_generated: "exported",
  violation_action_submitted: "actioned a finding",
  reviewer_violation_created: "flagged a finding",
  reviewer_violation_deleted: "removed a flag",
  feedback_submitted: "gave feedback",
  analysis_started: "started analysis",
  analysis_finished: "finished analysis",
};

const CLIP = 180;

function clip(s: string) {
  return s.length > CLIP ? `${s.slice(0, CLIP)}…` : s;
}

export function HistoryPanel({ submissionId }: { submissionId: string }) {
  const [rows, setRows] = useState<TrailRow[] | null>(null);
  const [denied, setDenied] = useState(false);

  useEffect(() => {
    documentTrail(submissionId)
      .then(r => setRows(r.trail))
      // 403 for a role without trail:view. Render nothing rather than an error
      // — the panel simply is not for them.
      .catch(() => setDenied(true));
  }, [submissionId]);

  if (denied) return null;
  if (!rows) {
    return <p className="px-4 py-3 text-sm text-muted-foreground">Loading history…</p>;
  }
  if (rows.length === 0) {
    return <p className="px-4 py-3 text-sm text-muted-foreground">No activity recorded yet.</p>;
  }

  return (
    <ol className="divide-y divide-border">
      {rows.map(r => (
        <li key={r.id} className="px-4 py-3 text-sm">
          <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
            <span className="text-xs tabular-nums text-muted-foreground">
              {r.at ? new Date(r.at).toLocaleString() : "—"}
            </span>
            <span className="font-medium">{VERB[r.event_type] ?? r.event_type}</span>
            {r.actor_role && (
              <span className="text-xs text-muted-foreground">({r.actor_role})</span>
            )}
          </div>

          {r.diff && (
            <div className="mt-1.5 space-y-0.5 rounded-sm border border-border bg-muted/30 p-2 font-mono text-xs">
              {r.diff.before
                ? <div className="text-sev-critical">− {clip(r.diff.before)}</div>
                : <div className="text-muted-foreground">− (new document)</div>}
              <div className="text-success">+ {clip(r.diff.after)}</div>
            </div>
          )}

          {typeof r.metadata?.reason === "string" && (
            <p className="mt-1 text-xs text-muted-foreground">
              reason: {r.metadata.reason as string}
            </p>
          )}
        </li>
      ))}
    </ol>
  );
}
