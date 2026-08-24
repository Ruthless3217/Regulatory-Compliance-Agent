"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { assignSubmission, assignmentWorkload, reassignAssignment } from "@/lib/api";
import type { AssignmentPriority, WorkloadRow } from "@/lib/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

const PRIORITIES: AssignmentPriority[] = ["low", "normal", "high", "urgent"];

type Props = {
  submissionId: string;
  /** Present when reassigning rather than assigning for the first time. */
  reassignFrom?: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
};

export function AssignDialog({ submissionId, reassignFrom, open, onOpenChange }: Props) {
  const router = useRouter();
  const [reviewers, setReviewers] = useState<WorkloadRow[]>([]);
  const [assignee, setAssignee] = useState("");
  const [priority, setPriority] = useState<AssignmentPriority>("normal");
  const [due, setDue] = useState("");
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) return;
    // The backend sorts least-loaded first, so the top of the list is the
    // reviewer with the most room. Work spreads by default rather than piling
    // onto whoever happens to be first alphabetically.
    assignmentWorkload()
      .then(r => setReviewers(r.reviewers.filter(x => x.is_active)))
      .catch(() => setReviewers([]));
  }, [open]);

  async function submit() {
    setBusy(true);
    setError(null);
    try {
      if (reassignFrom) {
        await reassignAssignment(reassignFrom, {
          assignee_id: assignee,
          note: note.trim() || null,
        });
      } else {
        await assignSubmission({
          submission_id: submissionId,
          assignee_id: assignee,
          priority,
          due_at: due ? new Date(due).toISOString() : null,
          note: note.trim() || null,
        });
      }
      onOpenChange(false);
      router.refresh();
    } catch (e) {
      const msg = e instanceof Error ? e.message : "";
      // 409 means it is already assigned. Say what to do about it rather than
      // echoing a status code at someone trying to hand out work.
      setError(
        msg.includes("409")
          ? "This document already has an active assignment. Reassign it instead."
          : "Could not assign. Please try again."
      );
    } finally {
      setBusy(false);
    }
  }

  const field = "mt-1 w-full rounded-md border border-border bg-background px-2 py-1.5 text-sm";
  const label = "block text-xs font-medium text-muted-foreground";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-[440px]">
        <DialogHeader>
          <DialogTitle>{reassignFrom ? "Reassign for review" : "Assign for review"}</DialogTitle>
          <DialogDescription>
            {reassignFrom
              ? "The current reviewer keeps their history; a fresh assignment opens for the new one."
              : "The document appears in this reviewer's bucket."}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div>
            <label className={label} htmlFor="assign-reviewer">Reviewer</label>
            <select
              id="assign-reviewer"
              className={field}
              value={assignee}
              onChange={e => setAssignee(e.target.value)}
            >
              <option value="">Select a reviewer…</option>
              {reviewers.map(r => (
                <option key={r.user_id} value={r.user_id}>
                  {r.username} — {r.open_count} open
                </option>
              ))}
            </select>
            {reviewers.length === 0 && (
              <p className="mt-1 text-xs text-muted-foreground">No active reviewers found.</p>
            )}
          </div>

          {!reassignFrom && (
            <div className="grid grid-cols-2 gap-3">
              <div>
                <label className={label} htmlFor="assign-priority">Priority</label>
                <select
                  id="assign-priority"
                  className={field}
                  value={priority}
                  onChange={e => setPriority(e.target.value as AssignmentPriority)}
                >
                  {PRIORITIES.map(p => <option key={p} value={p}>{p}</option>)}
                </select>
              </div>
              <div>
                <label className={label} htmlFor="assign-due">Due</label>
                <input
                  id="assign-due"
                  type="date"
                  className={field}
                  value={due}
                  onChange={e => setDue(e.target.value)}
                />
              </div>
            </div>
          )}

          <div>
            <label className={label} htmlFor="assign-note">Note to the reviewer</label>
            <textarea
              id="assign-note"
              className={field}
              rows={3}
              value={note}
              onChange={e => setNote(e.target.value)}
              placeholder="What should they focus on?"
            />
          </div>

          {error && <p className="text-sm text-sev-critical">{error}</p>}
        </div>

        <DialogFooter>
          <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={busy}>
            Cancel
          </Button>
          <Button onClick={submit} disabled={!assignee || busy}>
            {busy ? "Saving…" : reassignFrom ? "Reassign" : "Assign"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
