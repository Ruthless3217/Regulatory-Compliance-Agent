"use client";
import { useEffect, useState } from "react";
import {
  getDashboardSummary,
  getDashboardTimeseries,
  getTopRules,
  getViolationsByCategory,
  getViolationsBySeverity,
  listSubmissions,
} from "@/lib/mockApi";
import type { DashboardSummary, Submission, TimeseriesPoint, TopRule } from "@/lib/types";
import { KpiCards } from "@/components/dashboard/KpiCards";
import { ScoreTrend } from "@/components/dashboard/ScoreTrend";
import { SeverityDonut } from "@/components/dashboard/SeverityDonut";
import { CategoryBars } from "@/components/dashboard/CategoryBars";
import { TopRulesList } from "@/components/dashboard/TopRulesList";
import { RecentSubmissions } from "@/components/dashboard/RecentSubmissions";
import { Skeleton } from "@/components/ui/skeleton";

type NameValue = { name: string; value: number };

interface DashboardData {
  summary: DashboardSummary;
  severity: NameValue[];
  category: NameValue[];
  points: TimeseriesPoint[];
  rules: TopRule[];
  submissions: Submission[];
}

export default function DashboardPage() {
  const [data, setData] = useState<DashboardData | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      getDashboardSummary(),
      getViolationsBySeverity(),
      getViolationsByCategory(),
      getDashboardTimeseries("day"),
      getTopRules(10),
      listSubmissions(),
    ]).then(([summary, severity, category, timeseries, topRules, submissionsRes]) => {
      if (cancelled) return;
      setData({
        summary,
        severity,
        category,
        points: timeseries.points,
        rules: topRules.top_rules,
        submissions: submissionsRes.submissions,
      });
    });
    return () => {
      cancelled = true;
    };
  }, []);

  if (!data) {
    return <DashboardSkeleton />;
  }

  const criticalCount = data.severity.find((d) => d.name === "critical")?.value ?? 0;
  const isEmpty = data.submissions.length === 0;

  return (
    <div className="space-y-6 p-6">
      <div>
        <h1 className="text-lg font-semibold text-foreground">Dashboard</h1>
        <p className="text-sm text-muted-foreground">Compliance activity across all submissions.</p>
      </div>

      {isEmpty ? (
        <div className="flex h-64 flex-col items-center justify-center gap-1 rounded-lg border border-dashed border-border text-center">
          <p className="text-sm font-medium text-foreground">No submissions yet</p>
          <p className="text-sm text-muted-foreground">Submit content for review to see compliance activity here.</p>
        </div>
      ) : (
        <>
          <KpiCards summary={data.summary} criticalCount={criticalCount} />

          <div className="grid gap-4 lg:grid-cols-3">
            <div className="lg:col-span-2">
              <ScoreTrend points={data.points} />
            </div>
            <SeverityDonut data={data.severity} />
          </div>

          <div className="grid gap-4 lg:grid-cols-2">
            <CategoryBars data={data.category} />
            <TopRulesList rules={data.rules} />
          </div>

          <RecentSubmissions submissions={data.submissions} />
        </>
      )}
    </div>
  );
}

function DashboardSkeleton() {
  return (
    <div className="space-y-6 p-6">
      <div className="space-y-2">
        <Skeleton className="h-5 w-40" />
        <Skeleton className="h-4 w-64" />
      </div>
      <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} className="h-28 w-full" />
        ))}
      </div>
      <div className="grid gap-4 lg:grid-cols-3">
        <Skeleton className="h-64 w-full lg:col-span-2" />
        <Skeleton className="h-64 w-full" />
      </div>
      <div className="grid gap-4 lg:grid-cols-2">
        <Skeleton className="h-64 w-full" />
        <Skeleton className="h-64 w-full" />
      </div>
      <Skeleton className="h-56 w-full" />
    </div>
  );
}
