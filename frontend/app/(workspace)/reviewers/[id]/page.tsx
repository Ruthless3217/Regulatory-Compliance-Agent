"use client";
import { use, useEffect, useState } from "react";
import Link from "next/link";
import { ArrowLeft } from "lucide-react";
import { reviewerTrail } from "@/lib/api";
import type { ReviewerTrail } from "@/lib/types";
import { StatCard } from "@/components/ui/stat-card";
import { VERB } from "@/components/assignments/HistoryPanel";

const CLIP = 160;

function clip(s: string) {
  return s.length > CLIP ? `${s.slice(0, CLIP)}…` : s;
}

export default function ReviewerDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const [data, setData] = useState<ReviewerTrail | null>(null);
  const [denied, setDenied] = useState(false);

  useEffect(() => {
    reviewerTrail(id).then(setData).catch(() => setDenied(true));
  }, [id]);

  if (denied) {
    return (
      <div className="p-6">
        <p className="text-sm text-muted-foreground">
          You don&apos;t have access to reviewer activity.
        </p>
      </div>
    );
  }

  if (!data) return <div className="p-6 text-sm text-muted-foreground">Loading…</div>;

  return (
    <div className="space-y-6 p-6">
      <Link
        href="/reviewers"
        className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" /> All reviewers
      </Link>

      <div className="grid grid-cols-3 gap-3">
        <StatCard label="Open" value={String(data.stats.open)} />
        <StatCard label="Closed" value={String(data.stats.closed)} />
        <StatCard label="Total handled" value={String(data.stats.total)} />
      </div>

      <div>
        <h2 className="mb-2 text-sm font-medium">Activity</h2>
        {data.activity.length === 0 ? (
          <p className="text-sm text-muted-foreground">No recorded activity.</p>
        ) : (
          <ol className="divide-y divide-border rounded-md border border-border">
            {data.activity.map(r => (
              <li key={r.id} className="px-4 py-3 text-sm">
                <div className="flex flex-wrap items-baseline gap-x-2">
                  <span className="text-xs tabular-nums text-muted-foreground">
                    {r.at ? new Date(r.at).toLocaleString() : "—"}
                  </span>
                  <span className="font-medium">{VERB[r.event_type] ?? r.event_type}</span>
                </div>

                {r.diff && (
                  <div className="mt-1.5 space-y-0.5 rounded-sm border border-border bg-muted/30 p-2 font-mono text-xs">
                    {r.diff.before
                      ? <div className="text-sev-critical">− {clip(r.diff.before)}</div>
                      : <div className="text-muted-foreground">− (new document)</div>}
                    <div className="text-success">+ {clip(r.diff.after)}</div>
                  </div>
                )}
              </li>
            ))}
          </ol>
        )}
      </div>
    </div>
  );
}
