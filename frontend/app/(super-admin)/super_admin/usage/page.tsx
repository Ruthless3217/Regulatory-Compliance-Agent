"use client";
import * as React from "react";
import Link from "next/link";
import { Download } from "lucide-react";
import { PageHeader } from "@/components/ui/page-header";
import { Badge } from "@/components/ui/badge";
import { RangeChips } from "@/components/super-admin/RangeChips";
import { LoadingBlock, ErrorBlock, EmptyBlock, TableCard } from "@/components/super-admin/states";
import { useAsync } from "@/components/super-admin/useAsync";
import { fmtInt, fmtUsd, toNumber } from "@/components/super-admin/format";
import { formatDate } from "@/lib/format";
import { usageSummary, usageByDocument, usageCsvUrl } from "@/lib/api";

export default function ConsoleUsagePage() {
  const [days, setDays] = React.useState(30);
  const { data, loading, error } = useAsync(
    async () => {
      const [byUser, byDoc] = await Promise.all([usageSummary(days), usageByDocument(days)]);
      return { byUser, byDoc };
    },
    [days]
  );

  const byUser = data?.byUser ?? [];
  const byDoc = data?.byDoc ?? [];

  return (
    <div className="mx-auto max-w-7xl px-8 py-8">
      <PageHeader
        title="Usage & Cost"
        description="Token consumption and USD cost per user and per document. Re-runs are counted — every graded attempt burns tokens."
        actions={
          <div className="flex items-center gap-2">
            <RangeChips value={days} onChange={setDays} />
            {/* Plain <a download>, not a Next <Link>: this targets the backend
                CSV endpoint (already base-resolved), so let the browser stream it. */}
            <a
              href={usageCsvUrl(days)}
              download
              className="inline-flex h-8 items-center gap-1.5 rounded-md border border-border bg-background px-3 text-sm font-medium hover:bg-muted"
            >
              <Download className="h-3.5 w-3.5" />
              Export CSV
            </a>
          </div>
        }
      />

      {loading ? (
        <LoadingBlock label="Loading usage…" />
      ) : error ? (
        <ErrorBlock error={error} />
      ) : (
        <div className="space-y-8">
          {/* By user */}
          <section>
            <h2 className="mb-3 text-sm font-semibold tracking-tight">By user</h2>
            {byUser.length === 0 ? (
              <EmptyBlock title="No user spend in this window." />
            ) : (
              <TableCard>
                <thead>
                  <tr className="border-b border-border bg-muted/30 text-left">
                    <th className="px-4 py-3 micro-label">User</th>
                    <th className="px-4 py-3 micro-label w-[90px]">Role</th>
                    <th className="px-4 py-3 micro-label w-[110px] text-right">In tokens</th>
                    <th className="px-4 py-3 micro-label w-[110px] text-right">Out tokens</th>
                    <th className="px-4 py-3 micro-label w-[110px] text-right">Total tokens</th>
                    <th className="px-4 py-3 micro-label w-[90px] text-right">Cost</th>
                    <th className="px-4 py-3 micro-label w-[70px] text-right">Runs</th>
                    <th className="px-4 py-3 micro-label w-[80px] text-right">Sessions</th>
                  </tr>
                </thead>
                <tbody>
                  {byUser.map((r, i) => {
                    const total = r.total_tokens ?? toNumber(r.input_tokens) + toNumber(r.output_tokens);
                    return (
                      <tr key={`${r.username}-${i}`} className="border-b border-border last:border-0">
                        <td className="px-4 py-3 font-medium">
                          {r.username ?? <span className="text-muted-foreground">system</span>}
                        </td>
                        <td className="px-4 py-3">
                          {r.role ? <Badge>{String(r.role).replace(/_/g, " ")}</Badge> : "—"}
                        </td>
                        <td className="px-4 py-3 text-right font-mono">{fmtInt(r.input_tokens)}</td>
                        <td className="px-4 py-3 text-right font-mono">{fmtInt(r.output_tokens)}</td>
                        <td className="px-4 py-3 text-right font-mono">{fmtInt(total)}</td>
                        <td className="px-4 py-3 text-right font-mono font-medium">{fmtUsd(r.total_cost_usd)}</td>
                        <td className="px-4 py-3 text-right font-mono">{fmtInt(r.runs)}</td>
                        <td className="px-4 py-3 text-right font-mono text-muted-foreground">
                          {r.sessions === undefined ? "—" : fmtInt(r.sessions)}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </TableCard>
            )}
          </section>

          {/* By document */}
          <section>
            <h2 className="mb-3 text-sm font-semibold tracking-tight">By document</h2>
            {byDoc.length === 0 ? (
              <EmptyBlock title="No graded documents in this window." />
            ) : (
              <TableCard>
                <thead>
                  <tr className="border-b border-border bg-muted/30 text-left">
                    <th className="px-4 py-3 micro-label">Title</th>
                    <th className="px-4 py-3 micro-label w-[130px]">Graded by</th>
                    <th className="px-4 py-3 micro-label w-[110px] text-right">In tokens</th>
                    <th className="px-4 py-3 micro-label w-[110px] text-right">Out tokens</th>
                    <th className="px-4 py-3 micro-label w-[90px] text-right">Cost</th>
                    <th className="px-4 py-3 micro-label w-[70px] text-right">Runs</th>
                    <th className="px-4 py-3 micro-label w-[150px] text-right">Last run</th>
                  </tr>
                </thead>
                <tbody>
                  {byDoc.map((r) => (
                    <tr key={r.submission_id} className="border-b border-border last:border-0">
                      <td className="px-4 py-3">
                        <Link
                          href={`/super_admin/runs?submission=${r.submission_id}`}
                          className="font-medium hover:text-primary"
                          title="View this document's runs"
                        >
                          {r.title}
                        </Link>
                      </td>
                      <td className="px-4 py-3 text-muted-foreground">{r.graded_by ?? "—"}</td>
                      <td className="px-4 py-3 text-right font-mono">{fmtInt(r.input_tokens)}</td>
                      <td className="px-4 py-3 text-right font-mono">{fmtInt(r.output_tokens)}</td>
                      <td className="px-4 py-3 text-right font-mono font-medium">{fmtUsd(r.total_cost_usd)}</td>
                      <td className="px-4 py-3 text-right font-mono">{fmtInt(r.total_runs)}</td>
                      <td className="px-4 py-3 text-right font-mono text-[11px] text-muted-foreground">
                        {formatDate(r.last_run)}
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
