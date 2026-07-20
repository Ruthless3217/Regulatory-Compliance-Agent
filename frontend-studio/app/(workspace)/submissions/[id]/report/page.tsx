"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { getComplianceResults } from "@/lib/mockApi";
import { ScoreHero } from "@/components/report/ScoreHero";
import { KpiStrip } from "@/components/report/KpiStrip";
import { ViolationGroup } from "@/components/report/ViolationGroup";
import { ExportButton } from "@/components/report/ExportButton";
import { Skeleton } from "@/components/ui/skeleton";
import type { ComplianceResults } from "@/lib/types";

// Rendered inside the (workspace) shell AND under the sibling-owned
// submissions/[id] tab layout — this is just the report tab's body.
export default function ReportPage() {
  const params = useParams<{ id: string }>();
  const id = Array.isArray(params?.id) ? params.id[0] : (params?.id ?? "");

  const [result, setResult] = React.useState<ComplianceResults | null>(null);

  React.useEffect(() => {
    let alive = true;
    getComplianceResults(id).then((r) => {
      if (alive) setResult(r);
    });
    return () => {
      alive = false;
    };
  }, [id]);

  if (!result) {
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
