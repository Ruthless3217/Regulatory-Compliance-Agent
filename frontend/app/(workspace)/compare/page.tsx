import Link from "next/link";
import { ArrowUpRight, GitCompare } from "lucide-react";
import { listComparisons } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { PageHeader, PageHeaderMeta } from "@/components/ui/page-header";
import { StatusPill } from "@/components/ui/status-pill";
import { formatDate, sideLabel } from "@/lib/format";
import type { DocumentComparison } from "@/lib/types";

export const dynamic = "force-dynamic";

function comparisonStatusTone(status: string) {
  if (status === "completed") return "success" as const;
  if (status === "processing") return "info" as const;
  return "danger" as const;
}

/**
 * Secondary identifier for a row. Comparisons created before titles were derived
 * are all called "Untitled comparison", so their file names are the only thing
 * telling them apart. Null when the title already spells them out (extensions
 * dropped to match how the server derives it) or when neither side was a file.
 */
function sourceLine(c: DocumentComparison): string | null {
  if (!c.old_filename && !c.new_filename) return null;
  const stem = (n: string) => n.replace(/\.[^.]+$/, "");
  const line = `${stem(sideLabel(c, "old"))} → ${stem(sideLabel(c, "new"))}`;
  return line === c.title ? null : line;
}

export default async function ComparePage() {
  let items: DocumentComparison[] = [];
  let err: string | null = null;
  try {
    const data = await listComparisons();
    items = data.comparisons ?? [];
  } catch (e) {
    err = (e as Error).message;
  }

  return (
    <div className="mx-auto max-w-6xl px-8 py-8">
      <PageHeader
        title="Compare"
        description="Upload two versions of a document to see exactly what changed — word-level, side by side."
        actions={
          <Button asChild size="hero">
            <Link href="/compare/new">New comparison →</Link>
          </Button>
        }
        meta={<PageHeaderMeta label="Total" value={items.length} />}
      />

      {err ? (
        <div className="rounded-lg border border-sev-critical/30 bg-sev-critical/5 px-4 py-3 text-sm text-sev-critical">
          {err}
        </div>
      ) : items.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed border-border py-16 text-center">
          <GitCompare className="h-6 w-6 text-muted-foreground" />
          <p className="text-sm text-muted-foreground">No comparisons yet.</p>
          <Button asChild size="sm">
            <Link href="/compare/new">Create your first comparison →</Link>
          </Button>
        </div>
      ) : (
        <div className="overflow-hidden rounded-lg border border-border bg-background shadow-card">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-border bg-muted/30 text-left">
                <th className="px-3 py-2.5 micro-label">Title</th>
                <th className="px-3 py-2.5 micro-label w-[120px]">Status</th>
                <th className="px-3 py-2.5 micro-label w-[140px]">Created</th>
                <th className="px-3 py-2.5 w-[40px]"></th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr
                  key={c.id}
                  className="border-b border-border last:border-0 hover:bg-muted/40 transition-colors"
                >
                  <td className="px-3 py-2.5">
                    <Link
                      href={`/compare/${c.id}`}
                      target="_blank"
                      rel="noopener"
                      className="font-medium hover:text-primary"
                    >
                      {c.title}
                    </Link>
                    {sourceLine(c) && (
                      <div className="text-[11px] text-muted-foreground">{sourceLine(c)}</div>
                    )}
                    {/* The reason is already in the list payload; without it a
                        failed row says only "failed". */}
                    {c.status === "failed" && c.error_message && (
                      <div className="text-[11px] text-sev-critical">{c.error_message}</div>
                    )}
                  </td>
                  <td className="px-3 py-2.5">
                    <StatusPill tone={comparisonStatusTone(c.status)}>{c.status}</StatusPill>
                  </td>
                  <td className="px-3 py-2.5 font-mono text-[11px] text-muted-foreground">
                    {formatDate(c.created_at)}
                  </td>
                  <td className="px-3 py-2.5 text-right">
                    <Link
                      href={`/compare/${c.id}`}
                      target="_blank"
                      rel="noopener"
                      className="inline-flex h-6 w-6 items-center justify-center rounded-sm text-muted-foreground hover:bg-muted hover:text-foreground"
                    >
                      <ArrowUpRight className="h-3.5 w-3.5" />
                    </Link>
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
