"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { assignmentWorkload } from "@/lib/api";
import type { WorkloadRow } from "@/lib/types";
import { PageHeader } from "@/components/ui/page-header";
import { StatusPill } from "@/components/ui/status-pill";

export default function ReviewersPage() {
  const [rows, setRows] = useState<WorkloadRow[] | null>(null);
  const [denied, setDenied] = useState(false);

  useEffect(() => {
    assignmentWorkload()
      .then(r => setRows(r.reviewers))
      .catch(() => setDenied(true));
  }, []);

  if (denied) {
    return (
      <div className="p-6">
        <p className="text-sm text-muted-foreground">
          You don&apos;t have access to reviewer activity.
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4 p-6">
      <PageHeader
        title="Reviewers"
        description="Who is carrying what, and what they have done."
      />

      {!rows && <p className="text-sm text-muted-foreground">Loading…</p>}

      {rows && rows.length === 0 && (
        <p className="text-sm text-muted-foreground">No reviewers yet.</p>
      )}

      {rows && rows.length > 0 && (
        <div className="overflow-x-auto rounded-md border border-border">
          <table className="w-full text-sm">
            <thead className="bg-muted/40 text-left text-muted-foreground">
              <tr className="border-b border-border">
                <th className="px-3 py-2 font-medium">Reviewer</th>
                <th className="px-3 py-2 font-medium">Role</th>
                <th className="px-3 py-2 font-medium">Open</th>
                <th className="px-3 py-2 font-medium">Account</th>
              </tr>
            </thead>
            <tbody>
              {rows.map(r => (
                <tr key={r.user_id} className="border-b border-border last:border-0">
                  <td className="px-3 py-2">
                    <Link
                      href={`/reviewers/${r.user_id}`}
                      className="font-medium text-primary hover:underline"
                    >
                      {r.username}
                    </Link>
                  </td>
                  <td className="px-3 py-2 text-muted-foreground">{r.role}</td>
                  <td className="px-3 py-2 tabular-nums">{r.open_count}</td>
                  <td className="px-3 py-2">
                    {/* An inactive reviewer can still hold open assignments —
                        deactivation does not reassign their work. */}
                    <StatusPill tone={r.is_active ? "success" : "muted"}>
                      {r.is_active ? "active" : "inactive"}
                    </StatusPill>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
