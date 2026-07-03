import Link from "next/link";
import { notFound } from "next/navigation";
import { ArrowLeft } from "lucide-react";
import { getComparison } from "@/lib/api";
import { PageHeader, PageHeaderMeta } from "@/components/ui/page-header";
import { StatusPill } from "@/components/ui/status-pill";
import { CompareWorkspace } from "@/components/compare/CompareWorkspace";
import { formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";

function comparisonStatusTone(status: string) {
  if (status === "completed") return "success" as const;
  if (status === "processing") return "info" as const;
  return "danger" as const;
}

export default async function ComparisonDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  let comparison;
  try {
    comparison = await getComparison(id);
  } catch {
    notFound();
  }

  return (
    <div className="mx-auto max-w-[1400px] px-8 py-8">
      <Link
        href="/compare"
        className="mb-4 inline-flex items-center gap-1.5 text-xs text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-3.5 w-3.5" />
        Back to comparisons
      </Link>
      <PageHeader
        title={comparison.title}
        meta={
          <>
            <PageHeaderMeta
              label="Status"
              value={
                <StatusPill tone={comparisonStatusTone(comparison.status)}>
                  {comparison.status}
                </StatusPill>
              }
            />
            <PageHeaderMeta label="Created" value={formatDate(comparison.created_at)} />
          </>
        }
      />

      {comparison.status === "failed" ? (
        <div className="rounded-lg border border-sev-critical/30 bg-sev-critical/5 px-4 py-3 text-sm text-sev-critical">
          Comparison failed: {comparison.error_message ?? "Unknown error"}
        </div>
      ) : (
        <CompareWorkspace blocks={comparison.diff_result ?? []} />
      )}
    </div>
  );
}
