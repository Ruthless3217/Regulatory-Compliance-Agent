"use client";
import { useCallback, useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { Inbox } from "lucide-react";
import { listAssignments, listMyBucket } from "@/lib/api";
import type { ReviewAssignment, Submission } from "@/lib/types";
import { useAuth } from "@/components/auth/AuthProvider";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/ui/status-pill";
import { AssignDialog } from "./AssignDialog";

/** Added above the status tables rather than replacing them: status ("has this
 *  been analysed?") and assignment ("whose desk is it on?") are different
 *  questions, and folding one into the other would lose the answer to both. */

/** "unassigned" is not an assignment state — there is no row for it. It is a
 *  property of a submission, so the strip renders its own row type rather than
 *  faking a ReviewAssignment with an impossible status. */
type Row = {
  key: string;
  submission_id: string;
  status: ReviewAssignment["status"] | "unassigned";
  priority?: ReviewAssignment["priority"];
  due_at?: string | null;
  assignment_id?: string;
};

type Tab = { key: string; label: string; rows: Row[] };

function toRow(a: ReviewAssignment): Row {
  return {
    key: a.id,
    submission_id: a.submission_id,
    status: a.status,
    priority: a.priority,
    due_at: a.due_at,
    assignment_id: a.id,
  };
}

const STATUS_LABEL: Record<string, string> = {
  open: "Assigned",
  in_review: "In review",
  awaiting_signoff: "Awaiting sign-off",
};

export function BucketStrip({ submissions }: { submissions: Submission[] }) {
  const { me } = useAuth();
  const isAdmin = me?.role === "admin" || me?.role === "super_admin";

  const [assignments, setAssignments] = useState<ReviewAssignment[] | null>(null);
  const [active, setActive] = useState<string>("");
  const [assignFor, setAssignFor] = useState<string | null>(null);

  const load = useCallback(() => {
    const fetcher = isAdmin ? listAssignments() : listMyBucket();
    fetcher.then(r => setAssignments(r.assignments)).catch(() => setAssignments([]));
  }, [isAdmin]);

  useEffect(() => { load(); }, [load]);

  const titleFor = useMemo(() => {
    const map = new Map(submissions.map(s => [s.id, s.title]));
    return (id: string) => map.get(id) ?? id;
  }, [submissions]);

  const tabs: Tab[] = useMemo(() => {
    const rows = assignments ?? [];
    const byStatus = (s: ReviewAssignment["status"]) =>
      rows.filter(a => a.status === s).map(toRow);

    if (!isAdmin) {
      return [
        { key: "open", label: "My bucket", rows: byStatus("open") },
        { key: "in_review", label: "In review", rows: byStatus("in_review") },
        { key: "awaiting_signoff", label: "Awaiting sign-off", rows: byStatus("awaiting_signoff") },
      ];
    }

    // A document is unassigned when nothing active points at it. Computed from
    // the submissions the server already returned, so it never shows a document
    // the caller could not open anyway.
    const claimed = new Set(
      rows.filter(a => ["open", "in_review", "awaiting_signoff"].includes(a.status))
          .map(a => a.submission_id)
    );
    const unassigned: Row[] = submissions
      .filter(s => !claimed.has(s.id))
      .map(s => ({ key: `unassigned:${s.id}`, submission_id: s.id, status: "unassigned" }));

    return [
      { key: "unassigned", label: "Unassigned", rows: unassigned },
      { key: "awaiting_signoff", label: "Awaiting sign-off", rows: byStatus("awaiting_signoff") },
      { key: "in_review", label: "In review", rows: byStatus("in_review") },
      { key: "open", label: "Assigned", rows: byStatus("open") },
    ];
  }, [assignments, isAdmin, submissions]);

  useEffect(() => {
    if (!active && tabs.length) {
      // Land on the first tab that actually has work in it, so the strip opens
      // on something rather than on an empty default.
      setActive((tabs.find(t => t.rows.length > 0) ?? tabs[0]).key);
    }
  }, [tabs, active]);

  if (assignments === null) return null;
  const total = tabs.reduce((n, t) => n + t.rows.length, 0);
  if (total === 0 && !isAdmin) return null;

  const current = tabs.find(t => t.key === active) ?? tabs[0];

  return (
    <section className="mb-8 rounded-lg border border-border bg-background">
      <header className="flex items-center gap-2 border-b border-border px-4 py-2.5">
        <Inbox className="h-3.5 w-3.5 text-muted-foreground" />
        <h2 className="text-sm font-medium">{isAdmin ? "Review queue" : "My work"}</h2>
      </header>

      <div className="flex flex-wrap gap-1 border-b border-border px-2" role="tablist">
        {tabs.map(t => (
          <button
            key={t.key}
            role="tab"
            aria-selected={current?.key === t.key}
            onClick={() => setActive(t.key)}
            className={
              "-mb-px border-b-2 px-3 py-2 text-sm transition-colors " +
              (current?.key === t.key
                ? "border-primary font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:text-foreground")
            }
          >
            {t.label}
            <span className="ml-1.5 text-xs tabular-nums text-muted-foreground">
              {t.rows.length}
            </span>
          </button>
        ))}
      </div>

      {!current || current.rows.length === 0 ? (
        <p className="px-4 py-6 text-sm text-muted-foreground">
          Nothing here.
        </p>
      ) : (
        <ul className="divide-y divide-border">
          {current.rows.map(a => (
            <li key={a.key} className="flex items-center gap-3 px-4 py-2.5 text-sm">
              <Link
                href={`/submissions/${a.submission_id}`}
                className="min-w-0 flex-1 truncate font-medium text-primary hover:underline"
              >
                {titleFor(a.submission_id)}
              </Link>

              {a.priority && a.priority !== "normal" && (
                <span className="rounded bg-muted px-1.5 py-0.5 text-xs capitalize">
                  {a.priority}
                </span>
              )}
              {a.due_at && (
                <span className="whitespace-nowrap text-xs text-muted-foreground">
                  due {new Date(a.due_at).toLocaleDateString()}
                </span>
              )}
              {STATUS_LABEL[a.status] && (
                <StatusPill tone={a.status === "awaiting_signoff" ? "warning" : "info"}>
                  {STATUS_LABEL[a.status]}
                </StatusPill>
              )}

              {isAdmin && a.status === "unassigned" && (
                <Button size="sm" variant="outline"
                  onClick={() => setAssignFor(a.submission_id)}>
                  Assign
                </Button>
              )}
            </li>
          ))}
        </ul>
      )}

      {assignFor && (
        <AssignDialog
          submissionId={assignFor}
          open
          onOpenChange={o => { if (!o) { setAssignFor(null); load(); } }}
        />
      )}
    </section>
  );
}
