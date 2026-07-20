"use client";

import * as React from "react";
import { useParams } from "next/navigation";
import { getSubmission, getComplianceResults } from "@/lib/mockApi";
import { groupViolations } from "@/lib/violationGroups";
import type { ComplianceResults, Submission } from "@/lib/types";
import { DocumentPane } from "@/components/review/DocumentPane";
import { ViolationsPane } from "@/components/review/ViolationsPane";
import { Skeleton } from "@/components/ui/skeleton";

// Review tab body — rendered inside the sibling-owned submissions/[id] tab
// layout. Loads the submission + its compliance results and splits findings
// into scored groups (document highlights + violation cards) vs. the
// suppressed "Needs review" lane.
export default function SubmissionReviewPage() {
  const params = useParams<{ id: string }>();
  const id = Array.isArray(params?.id) ? params.id[0] : params?.id ?? "";

  const [submission, setSubmission] = React.useState<Submission | null>(null);
  const [results, setResults] = React.useState<ComplianceResults | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [selectedId, setSelectedId] = React.useState<string | null>(null);

  React.useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([getSubmission(id), getComplianceResults(id)]).then(([s, r]) => {
      if (cancelled) return;
      setSubmission(s);
      setResults(r);
      setLoading(false);
    });
    return () => {
      cancelled = true;
    };
  }, [id]);

  const { groups, suppressed } = React.useMemo(
    () => groupViolations(results?.violations ?? []),
    [results]
  );

  if (loading) {
    return (
      <div className="grid h-full grid-cols-2 divide-x divide-border overflow-hidden">
        <div className="space-y-3 p-8">
          <Skeleton className="h-5 w-2/3" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-full" />
          <Skeleton className="h-4 w-5/6" />
          <Skeleton className="h-4 w-4/5" />
        </div>
        <div className="space-y-3 p-4">
          <Skeleton className="h-8 w-1/2" />
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-28 w-full" />
          <Skeleton className="h-28 w-full" />
        </div>
      </div>
    );
  }

  if (!submission) {
    return (
      <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
        Submission not found.
      </div>
    );
  }

  return (
    <div className="grid h-full grid-cols-2 divide-x divide-border overflow-hidden">
      <div className="overflow-y-auto">
        <DocumentPane
          content={submission.original_content ?? ""}
          groups={groups}
          selectedId={selectedId}
          onSelect={setSelectedId}
        />
      </div>
      <div className="min-h-0 overflow-hidden">
        <ViolationsPane groups={groups} suppressed={suppressed} selectedId={selectedId} onSelect={setSelectedId} />
      </div>
    </div>
  );
}
