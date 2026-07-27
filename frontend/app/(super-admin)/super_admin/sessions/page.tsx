"use client";
import * as React from "react";
import { PageHeader } from "@/components/ui/page-header";
import { StatusPill } from "@/components/ui/status-pill";
import { RangeChips } from "@/components/super-admin/RangeChips";
import { LoadingBlock, ErrorBlock, EmptyBlock, TableCard } from "@/components/super-admin/states";
import { useAsync } from "@/components/super-admin/useAsync";
import { fmtDurationSec, fmtAgo } from "@/components/super-admin/format";
import { formatDate } from "@/lib/format";
import { listSessions } from "@/lib/api";
import type { SessionRow } from "@/lib/types";

function sessionSeconds(s: SessionRow): number | null {
  if (s.duration_seconds !== null && s.duration_seconds !== undefined) return s.duration_seconds;
  const login = new Date(s.login_at).getTime();
  if (Number.isNaN(login)) return null;
  const end = s.last_seen_at ? new Date(s.last_seen_at).getTime() : Date.now();
  if (Number.isNaN(end)) return null;
  return Math.max(0, (end - login) / 1000);
}

function statusTone(status: string) {
  const s = status.toLowerCase();
  if (s === "active") return "success" as const;
  if (s === "expired") return "warning" as const;
  return "muted" as const;
}

export default function ConsoleSessionsPage() {
  const [days, setDays] = React.useState(7);
  const { data, loading, error } = useAsync(() => listSessions(days), [days]);
  const sessions = data ?? [];
  const online = sessions.filter((s) => s.status.toLowerCase() === "active");

  return (
    <div className="mx-auto max-w-7xl px-8 py-8">
      <PageHeader
        title="Sessions"
        description="Who's online now and historical login sessions — IP, login time, last seen, and active duration."
        actions={<RangeChips value={days} onChange={setDays} />}
      />

      {loading ? (
        <LoadingBlock label="Loading sessions…" />
      ) : error ? (
        <ErrorBlock error={error} />
      ) : (
        <div className="space-y-8">
          {/* Online now */}
          <section>
            <div className="mb-3 flex items-center gap-2">
              <h2 className="text-sm font-semibold tracking-tight">Online now</h2>
              <StatusPill tone={online.length > 0 ? "success" : "muted"} pulse={online.length > 0}>
                {online.length} active
              </StatusPill>
            </div>
            {online.length === 0 ? (
              <div className="rounded-lg border border-border bg-background px-4 py-6 text-sm text-muted-foreground shadow-card">
                No one is currently signed in.
              </div>
            ) : (
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {online.map((s) => (
                  <div key={s.id} className="rounded-lg border border-border bg-background p-4 shadow-card">
                    <div className="flex items-center justify-between">
                      <span className="font-medium">{s.username ?? "—"}</span>
                      <span className="inline-block h-2 w-2 animate-pulse rounded-full bg-success" />
                    </div>
                    <div className="mt-1 font-mono text-[11px] text-muted-foreground">{s.ip ?? "—"}</div>
                    <div className="mt-2 flex items-baseline justify-between text-[11px] text-muted-foreground">
                      <span>seen {fmtAgo(s.last_seen_at ?? s.login_at)}</span>
                      <span className="font-mono">{fmtDurationSec(sessionSeconds(s))}</span>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </section>

          {/* History */}
          <section>
            <h2 className="mb-3 text-sm font-semibold tracking-tight">Session history</h2>
            {sessions.length === 0 ? (
              <EmptyBlock title="No sessions in this window." />
            ) : (
              <TableCard>
                <thead>
                  <tr className="border-b border-border bg-muted/30 text-left">
                    <th className="px-4 py-3 micro-label">User</th>
                    <th className="px-4 py-3 micro-label w-[140px]">IP</th>
                    <th className="px-4 py-3 micro-label w-[150px]">Login</th>
                    <th className="px-4 py-3 micro-label w-[150px]">Last seen</th>
                    <th className="px-4 py-3 micro-label w-[100px] text-right">Duration</th>
                    <th className="px-4 py-3 micro-label w-[100px]">Status</th>
                  </tr>
                </thead>
                <tbody>
                  {sessions.map((s) => (
                    <tr key={s.id} className="border-b border-border last:border-0">
                      <td className="px-4 py-3 font-medium">{s.username ?? "—"}</td>
                      <td className="px-4 py-3 font-mono text-[12px] text-muted-foreground">{s.ip ?? "—"}</td>
                      <td className="px-4 py-3 font-mono text-[11px] text-muted-foreground">
                        {formatDate(s.login_at)}
                      </td>
                      <td className="px-4 py-3 font-mono text-[11px] text-muted-foreground">
                        {s.last_seen_at ? formatDate(s.last_seen_at) : "—"}
                      </td>
                      <td className="px-4 py-3 text-right font-mono">{fmtDurationSec(sessionSeconds(s))}</td>
                      <td className="px-4 py-3">
                        <StatusPill tone={statusTone(s.status)} pulse={s.status.toLowerCase() === "active"}>
                          <span className="text-[10px]">{s.status}</span>
                        </StatusPill>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </TableCard>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
