"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import {
  assignmentForSubmission,
  completeAssignment,
  sendBackAssignment,
  startAssignment,
} from "@/lib/api";
import type { AssignmentStatus, ReviewAssignment } from "@/lib/types";
import { useAuth } from "@/components/auth/AuthProvider";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/ui/status-pill";
import { AssignDialog } from "./AssignDialog";

const LABEL: Record<AssignmentStatus, string> = {
  open: "Assigned",
  in_review: "In review",
  awaiting_signoff: "Awaiting sign-off",
  closed: "Closed",
  superseded: "Reassigned",
  cancelled: "Cancelled",
};

const TONE: Record<AssignmentStatus, "neutral" | "info" | "warning" | "success" | "muted"> = {
  open: "info",
  in_review: "info",
  awaiting_signoff: "warning",
  closed: "success",
  superseded: "muted",
  cancelled: "muted",
};

export function AssignmentBanner({ submissionId }: { submissionId: string }) {
  const router = useRouter();
  const { me } = useAuth();
  const [active, setActive] = useState<ReviewAssignment | null>(null);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reassigning, setReassigning] = useState(false);

  const load = useCallback(() => {
    assignmentForSubmission(submissionId)
      .then(r => setActive(r.active))
      .catch(() => setActive(null))
      .finally(() => setLoaded(true));
  }, [submissionId]);

  useEffect(() => { load(); }, [load]);

  const isAdmin = me?.role === "admin" || me?.role === "super_admin";

  async function run(fn: () => Promise<unknown>) {
    setBusy(true);
    try {
      await fn();
      load();
      router.refresh();
    } finally {
      setBusy(false);
    }
  }

  if (!loaded) return null;

  if (!active) {
    // Unassigned. Only worth saying so to someone who can do something about it.
    if (!isAdmin) return null;
    return (
      <>
        <div className="flex items-center gap-3 border-b border-border bg-muted/40 px-4 py-2 text-sm">
          <StatusPill tone="neutral">Unassigned</StatusPill>
          <span className="text-muted-foreground">
            No reviewer is working on this document.
          </span>
          <Button size="sm" className="ml-auto" onClick={() => setReassigning(true)}>
            Assign
          </Button>
        </div>
        <AssignDialog
          submissionId={submissionId}
          open={reassigning}
          onOpenChange={o => { setReassigning(o); if (!o) load(); }}
        />
      </>
    );
  }

  const isAssignee = !!me?.id && me.id === active.assignee_id;
  const overdue =
    !!active.due_at && new Date(active.due_at) < new Date() &&
    active.status !== "closed";

  return (
    <>
      <div className="flex flex-wrap items-center gap-3 border-b border-border bg-muted/40 px-4 py-2 text-sm">
        <StatusPill tone={TONE[active.status]}>{LABEL[active.status]}</StatusPill>

        {active.due_at && (
          <span className={overdue ? "font-medium text-sev-critical" : "text-muted-foreground"}>
            due {new Date(active.due_at).toLocaleDateString()}
            {overdue && " — overdue"}
          </span>
        )}

        {active.priority !== "normal" && (
          <span className="rounded bg-background px-1.5 py-0.5 text-xs capitalize">
            {active.priority}
          </span>
        )}

        {active.note && (
          <span className="max-w-[38ch] truncate text-muted-foreground" title={active.note}>
            “{active.note}”
          </span>
        )}

        {active.outcome_note && active.status === "in_review" && (
          <span className="max-w-[38ch] truncate text-sev-critical" title={active.outcome_note}>
            sent back: {active.outcome_note}
          </span>
        )}

        <div className="ml-auto flex gap-2">
          {isAssignee && active.status === "open" && (
            <Button size="sm" disabled={busy}
              onClick={() => run(() => startAssignment(active.id))}>
              Start review
            </Button>
          )}
          {isAssignee && active.status === "in_review" && (
            <Button size="sm" disabled={busy}
              onClick={() => run(() => completeAssignment(active.id))}>
              Mark complete
            </Button>
          )}
          {isAdmin && active.status === "awaiting_signoff" && (
            <Button size="sm" variant="outline" disabled={busy}
              onClick={() => {
                const reason = window.prompt("Why is this going back to the reviewer?");
                if (reason?.trim()) run(() => sendBackAssignment(active.id, reason));
              }}>
              Send back
            </Button>
          )}
          {isAdmin && active.status !== "closed" && (
            <Button size="sm" variant="ghost" disabled={busy}
              onClick={() => setReassigning(true)}>
              Reassign
            </Button>
          )}
        </div>
      </div>

      <AssignDialog
        submissionId={submissionId}
        reassignFrom={active.id}
        open={reassigning}
        onOpenChange={o => { setReassigning(o); if (!o) load(); }}
      />
    </>
  );
}
