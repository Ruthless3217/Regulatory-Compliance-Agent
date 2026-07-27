"use client";
import * as React from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { PageHeader } from "@/components/ui/page-header";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { StatusPill } from "@/components/ui/status-pill";
import { RangeChips } from "@/components/super-admin/RangeChips";
import { LoadingBlock, ErrorBlock, EmptyBlock, TableCard } from "@/components/super-admin/states";
import { useAsync } from "@/components/super-admin/useAsync";
import { fmtInt, fmtUsd, fmtDurationMs } from "@/components/super-admin/format";
import { formatDate } from "@/lib/format";
import { listRuns, submissionRuns } from "@/lib/api";
import type { RunRow } from "@/lib/types";

const STATUSES = ["all", "running", "completed", "needs_review", "failed"] as const;
type StatusFilter = (typeof STATUSES)[number];

const selectClass =
  "flex h-8 rounded-md border border-border bg-background px-2.5 text-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary";

function statusTone(status: string) {
  const s = status.toLowerCase();
  if (s === "completed") return "success" as const;
  if (s === "running") return "info" as const;
  if (s === "needs_review") return "warning" as const;
  if (s === "failed") return "danger" as const;
  return "neutral" as const;
}

function triggerTone(t: string) {
  if (t === "stream") return "primary" as const;
  if (t === "async") return "low" as const;
  return "default" as const;
}

function RunsInner() {
  const params = useSearchParams();
  const submissionId = params.get("submission");

  const [days, setDays] = React.useState(30);
  const [user, setUser] = React.useState("");
  const [status, setStatus] = React.useState<StatusFilter>("all");
  const [userQuery, setUserQuery] = React.useState("");

  // Debounce the free-text user filter so we don't refetch on every keystroke.
  React.useEffect(() => {
    const t = setTimeout(() => setUserQuery(user.trim()), 350);
    return () => clearTimeout(t);
  }, [user]);

  const { data, loading, error } = useAsync<RunRow[]>(
    () =>
      submissionId
        ? submissionRuns(submissionId)
        : listRuns({
            days,
            user: userQuery || undefined,
            status: status === "all" ? undefined : status,
          }),
    [submissionId, days, userQuery, status]
  );

  const runs = data ?? [];

  return (
    <div className="mx-auto max-w-[90rem] px-8 py-8">
      <PageHeader
        title="Analysis runs"
        description="Every graded run — including re-runs and fail-closed runs that still cost tokens. Trigger, status, degraded reason, duration, tokens and cost."
        actions={!submissionId ? <RangeChips value={days} onChange={setDays} /> : undefined}
      />

      {submissionId && (
        <div className="mb-4 flex items-center gap-2 rounded-md border border-border bg-muted/30 px-3 py-2 text-sm">
          <span className="text-muted-foreground">Showing runs for one document.</span>
          <Link href="/super_admin/runs" className="font-medium text-primary hover:underline">
            Clear filter
          </Link>
        </div>
      )}

      {!submissionId && (
        <div className="mb-4 flex flex-wrap items-center gap-2">
          <Input
            value={user}
            onChange={(e) => setUser(e.target.value)}
            placeholder="Filter by username…"
            className="h-8 w-56"
          />
          <select
            className={selectClass}
            value={status}
            onChange={(e) => setStatus(e.target.value as StatusFilter)}
          >
            {STATUSES.map((s) => (
              <option key={s} value={s}>
                {s === "all" ? "All statuses" : s.replace(/_/g, " ")}
              </option>
            ))}
          </select>
        </div>
      )}

      {loading ? (
        <LoadingBlock label="Loading runs…" />
      ) : error ? (
        <ErrorBlock error={error} />
      ) : runs.length === 0 ? (
        <EmptyBlock title="No runs match these filters." />
      ) : (
        <TableCard>
          <thead>
            <tr className="border-b border-border bg-muted/30 text-left">
              <th className="px-3 py-3 micro-label">Document</th>
              <th className="px-3 py-3 micro-label w-[120px]">User</th>
              <th className="px-3 py-3 micro-label w-[64px] text-right">Run</th>
              <th className="px-3 py-3 micro-label w-[90px]">Trigger</th>
              <th className="px-3 py-3 micro-label w-[120px]">Status</th>
              <th className="px-3 py-3 micro-label w-[150px]">Degraded reason</th>
              <th className="px-3 py-3 micro-label w-[90px] text-right">Duration</th>
              <th className="px-3 py-3 micro-label w-[90px] text-right">In</th>
              <th className="px-3 py-3 micro-label w-[90px] text-right">Out</th>
              <th className="px-3 py-3 micro-label w-[90px] text-right">Cost</th>
              <th className="px-3 py-3 micro-label w-[140px] text-right">Started</th>
            </tr>
          </thead>
          <tbody>
            {runs.map((r) => (
              <tr key={r.id} className="border-b border-border last:border-0">
                <td className="px-3 py-3">
                  <span className="line-clamp-1" title={r.submission_title ?? r.submission_id}>
                    {r.submission_title ?? (
                      <span className="font-mono text-[11px] text-muted-foreground">
                        {r.submission_id.slice(0, 8)}
                      </span>
                    )}
                  </span>
                </td>
                <td className="px-3 py-3 text-muted-foreground">{r.username ?? r.triggered_by ?? "—"}</td>
                <td className="px-3 py-3 text-right">
                  <span className="font-mono">{r.run_number}</span>
                  {r.is_rerun && (
                    <Badge className="ml-1.5" tone="medium">
                      re-run
                    </Badge>
                  )}
                </td>
                <td className="px-3 py-3">
                  <Badge tone={triggerTone(r.trigger_source)}>{r.trigger_source}</Badge>
                </td>
                <td className="px-3 py-3">
                  <StatusPill tone={statusTone(r.status)} pulse={r.status === "running"}>
                    <span className="text-[10px]">{r.status.replace(/_/g, " ")}</span>
                  </StatusPill>
                </td>
                <td className="px-3 py-3 text-[12px] text-muted-foreground">
                  {r.degraded_reason ? (
                    <span className="text-sev-high">{r.degraded_reason}</span>
                  ) : (
                    "—"
                  )}
                </td>
                <td className="px-3 py-3 text-right font-mono text-[12px]">{fmtDurationMs(r.duration_ms)}</td>
                <td className="px-3 py-3 text-right font-mono">{fmtInt(r.prompt_tokens)}</td>
                <td className="px-3 py-3 text-right font-mono">{fmtInt(r.completion_tokens)}</td>
                <td className="px-3 py-3 text-right font-mono font-medium">{fmtUsd(r.total_cost_usd, 3)}</td>
                <td className="px-3 py-3 text-right font-mono text-[11px] text-muted-foreground">
                  {formatDate(r.started_at)}
                </td>
              </tr>
            ))}
          </tbody>
        </TableCard>
      )}
    </div>
  );
}

export default function ConsoleRunsPage() {
  // `useSearchParams` requires a Suspense boundary during prerender.
  return (
    <React.Suspense fallback={<div className="px-8 py-8" />}>
      <RunsInner />
    </React.Suspense>
  );
}
