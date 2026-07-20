"use client";

import * as React from "react";
import { useParams, useSearchParams } from "next/navigation";
import { getComplianceResults } from "@/lib/mockApi";
import { complianceResultsDegraded } from "@/lib/mock";
import { ScoreHero } from "@/components/report/ScoreHero";
import { KpiStrip } from "@/components/report/KpiStrip";
import { ViolationGroup } from "@/components/report/ViolationGroup";
import { ExportButton } from "@/components/report/ExportButton";
import { Skeleton } from "@/components/ui/skeleton";
import { ErrorCard } from "@/components/ui/error-card";
import type { ComplianceResults } from "@/lib/types";

function ReportSkeleton() {
  return (
    <div className="space-y-6">
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-28 w-full" />
      <div className="flex items-center justify-between">
        <Skeleton className="h-5 w-24" />
        <Skeleton className="h-8 w-28" />
      </div>
      <div className="space-y-3">
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
        <Skeleton className="h-16 w-full" />
      </div>
    </div>
  );
}

// Rendered inside the (workspace) shell AND under the sibling-owned
// submissions/[id] tab layout — this is just the report tab's body.
// `?state=degraded` swaps in the waiting-for-review fixture so the
// no-score "Needs review" path (see ScoreHero) stays reachable on demand.
function ReportPageBody() {
  const params = useParams<{ id: string }>();
  const id = Array.isArray(params?.id) ? params.id[0] : (params?.id ?? "");
  const searchParams = useSearchParams();
  const degraded = searchParams?.get("state") === "degraded";

  const [result, setResult] = React.useState<ComplianceResults | null>(null);
  const [error, setError] = React.useState<string | null>(null);
  const [retryKey, setRetryKey] = React.useState(0);

  React.useEffect(() => {
    let alive = true;
    setError(null);
    setResult(null);

    if (degraded) {
      setResult(complianceResultsDegraded);
      return () => {
        alive = false;
      };
    }

    getComplianceResults(id)
      .then((r) => {
        if (alive) setResult(r);
      })
      .catch(() => {
        if (alive) setError("Couldn't load the compliance report. Please try again.");
      });
    return () => {
      alive = false;
    };
  }, [id, degraded, retryKey]);

  if (error) {
    return <ErrorCard message={error} onRetry={() => setRetryKey((k) => k + 1)} />;
  }

  if (!result) {
    return <ReportSkeleton />;
  }

  return (
    <div className="space-y-6">
      <ScoreHero result={result} />
      <KpiStrip violations={result.violations} />
      <div className="flex items-center justify-between">
        <h2 className="text-sm font-semibold">Violations</h2>
        <ExportButton />
      </div>
      <ViolationGroup violations={result.violations} />
    </div>
  );
}

export default function ReportPage() {
  return (
    <React.Suspense fallback={<ReportSkeleton />}>
      <ReportPageBody />
    </React.Suspense>
  );
}
