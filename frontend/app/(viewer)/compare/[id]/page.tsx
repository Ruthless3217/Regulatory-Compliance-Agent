import Link from "next/link";
import { notFound } from "next/navigation";
import { getComparison } from "@/lib/api";
import { ViewerShell } from "@/components/compare-viewer/ViewerShell";

export const dynamic = "force-dynamic";

export default async function ComparisonViewerPage({
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

  // A failed *diff* (not merely a failed render) has no content to view — mirror
  // the old detail page and show a full-viewport failure banner with a way back.
  if (comparison.status === "failed") {
    return (
      <div className="flex h-full w-full flex-col items-center justify-center gap-4 p-8 text-center">
        <div className="max-w-lg rounded-lg border border-sev-critical/30 bg-sev-critical/5 px-4 py-3 text-sm text-sev-critical">
          Comparison failed: {comparison.error_message ?? "Unknown error"}
        </div>
        <Link href="/compare" className="text-xs text-muted-foreground hover:text-foreground">
          ← Back to comparisons
        </Link>
      </div>
    );
  }

  return <ViewerShell comparison={comparison} />;
}
