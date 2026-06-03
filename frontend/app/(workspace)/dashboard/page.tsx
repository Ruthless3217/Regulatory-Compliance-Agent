import Link from "next/link";
import { FileText, AlertOctagon, Wrench, CalendarClock } from "lucide-react";
import {
  getDashboardSummary,
  getViolationsByCategory,
  getViolationsBySeverity,
  getDashboardTimeseries,
  getTopRules,
} from "@/lib/api";
import { CategoryRadar } from "@/components/dashboard/CategoryRadar";
import { SeverityDonut } from "@/components/dashboard/SeverityDonut";
import { ScoreHeroCard } from "@/components/dashboard/ScoreHeroCard";
import { ScoreTrend, VolumeTrend } from "@/components/dashboard/TimeseriesCharts";
import { TopRulesList } from "@/components/dashboard/TopRulesList";
import { StatCard } from "@/components/ui/stat-card";
import { PageHeader, PageHeaderMeta } from "@/components/ui/page-header";
import { StatusPill, statusTone } from "@/components/ui/status-pill";
import { formatDate } from "@/lib/format";
import type { TimeseriesPoint, TopRule } from "@/lib/types";

export const dynamic = "force-dynamic";

interface Summary {
  stats?: {
    total_submissions?: number;
    total_violations?: number;
    active_rules?: number;
    average_score?: number;
    total_checks?: number;
    submissions_this_week?: number;
    critical_count?: number;
    auto_fix_rate?: number;
    auto_fixable_count?: number;
  };
  grade_distribution?: Record<string, number>;
  recent_submissions?: { id: string; title: string; status: string; submitted_at: string }[];
}

const GRADES = ["A", "B", "C", "D", "F"] as const;
const GRADE_COLOR: Record<string, string> = {
  A: "hsl(var(--success))",
  B: "hsl(var(--primary))",
  C: "hsl(var(--sev-medium))",
  D: "hsl(var(--sev-high))",
  F: "hsl(var(--sev-critical))",
};

export default async function DashboardPage() {
  let summary: Summary | null = null;
  let byCat: { category: string; count: number }[] = [];
  let bySev: { severity: string; count: number }[] = [];
  let err: string | null = null;
  try {
    [summary, byCat, bySev] = await Promise.all([
      getDashboardSummary() as Promise<Summary>,
      getViolationsByCategory().then((r: unknown) =>
        ((r as { violations_by_category?: { category: string; count: number }[] }).violations_by_category) ?? []
      ),
      getViolationsBySeverity().then((r: unknown) =>
        ((r as { violations_by_severity?: { severity: string; count: number }[] }).violations_by_severity) ?? []
      ),
    ]);
  } catch (e) {
    err = (e as Error).message;
  }

  // Trend + top-rules are best-effort: a failure here must not blank the page.
  let points: TimeseriesPoint[] = [];
  let topRules: TopRule[] = [];
  try {
    points = (await getDashboardTimeseries("day")).points ?? [];
  } catch {
    /* trend charts render an empty state */
  }
  try {
    topRules = (await getTopRules(10)).top_rules ?? [];
  } catch {
    /* top-rules renders an empty state */
  }

  const stats = summary?.stats ?? {};
  const recent = summary?.recent_submissions ?? [];
  const gradeDist = summary?.grade_distribution ?? {};
  const gradeMax = Math.max(1, ...GRADES.map((g) => gradeDist[g] ?? 0));
  const avgScore = stats.average_score ?? 0;
  const hasData =
    (stats.total_submissions ?? 0) > 0 ||
    (stats.total_violations ?? 0) > 0 ||
    byCat.length > 0 ||
    bySev.length > 0;

  return (
    <div className="mx-auto max-w-7xl px-8 py-8">
      <PageHeader
        title="Dashboard"
        description="Compliance health across all submissions — score, violations by regulator and severity, updated as analyses complete."
        meta={
          <>
            <PageHeaderMeta label="Submissions" value={stats.total_submissions ?? 0} />
            <PageHeaderMeta label="Violations" value={stats.total_violations ?? 0} />
            <PageHeaderMeta label="Avg score" value={avgScore.toFixed(1)} />
            <PageHeaderMeta label="Active rules" value={stats.active_rules ?? 0} />
          </>
        }
      />

      {err ? (
        <div className="rounded-lg border border-border bg-background p-8 shadow-card">
          <div className="text-xs font-semibold uppercase tracking-wide text-sev-critical">API unreachable</div>
          <h2 className="mt-2 text-xl font-semibold">Dashboard data couldn&rsquo;t load.</h2>
          <p className="mt-2 text-sm text-muted-foreground">{err}</p>
        </div>
      ) : !hasData ? (
        <div className="rounded-lg border border-border bg-background p-12 text-center shadow-card">
          <div className="mb-3 text-xs font-semibold uppercase tracking-wide text-muted-foreground">No analyses yet</div>
          <h2 className="text-xl font-semibold">Charts will appear after your first compliance check.</h2>
          <p className="mx-auto mt-3 max-w-md text-sm text-muted-foreground">
            The dashboard plots violations by regulator, severity distribution, and grade breakdown across submissions.
          </p>
        </div>
      ) : (
        <div className="space-y-5">
          {/* Hero band: blue score card + KPI cluster */}
          <div className="grid gap-5 lg:grid-cols-3">
            <ScoreHeroCard
              score={avgScore}
              totalChecks={stats.total_checks ?? 0}
              submissionsThisWeek={stats.submissions_this_week ?? 0}
            />
            <div className="grid grid-cols-2 gap-4 lg:col-span-2">
              <StatCard
                label="Submissions"
                value={stats.total_submissions ?? 0}
                sub={`${stats.submissions_this_week ?? 0} this week`}
                icon={<FileText className="h-3.5 w-3.5" />}
              />
              <StatCard
                label="Violations caught"
                value={stats.total_violations ?? 0}
                sub="across all checks"
                icon={<AlertOctagon className="h-3.5 w-3.5" />}
              />
              <StatCard
                label="Open critical"
                value={stats.critical_count ?? 0}
                sub="severity = critical"
                tone={stats.critical_count && stats.critical_count > 0 ? "danger" : "default"}
                icon={<AlertOctagon className="h-3.5 w-3.5" />}
              />
              <StatCard
                label="Auto-fix rate"
                value={stats.auto_fix_rate !== undefined ? `${stats.auto_fix_rate}%` : "—"}
                sub={`${stats.auto_fixable_count ?? 0}/${stats.total_violations ?? 0} fixable`}
                tone="primary"
                icon={<Wrench className="h-3.5 w-3.5" />}
              />
            </div>
          </div>

          {/* Trends band */}
          <div className="grid gap-5 lg:grid-cols-2">
            <ScoreTrend points={points} />
            <VolumeTrend points={points} />
          </div>

          {/* Charts band */}
          <div className="grid gap-5 lg:grid-cols-2">
            <CategoryRadar rows={byCat} />
            <SeverityDonut rows={bySev} />
          </div>

          {/* Top violated rules */}
          <TopRulesList rules={topRules} />

          {/* Detail band: recent submissions + grade distribution */}
          <div className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
            <div className="rounded-lg border border-border bg-background shadow-card">
              <div className="flex items-center justify-between border-b border-border px-5 py-3">
                <h3 className="text-base font-semibold tracking-tight">Recent submissions</h3>
                <Link href="/" className="text-xs font-medium text-primary hover:underline">
                  View all →
                </Link>
              </div>
              {recent.length === 0 ? (
                <div className="px-5 py-8 text-center text-sm text-muted-foreground">No submissions yet.</div>
              ) : (
                <ul className="divide-y divide-border">
                  {recent.map((s) => (
                    <li key={s.id} className="flex items-center gap-3 px-5 py-3">
                      <CalendarClock className="h-4 w-4 shrink-0 text-muted-foreground" />
                      <div className="min-w-0 flex-1">
                        <Link
                          href={`/submissions/${s.id}`}
                          className="block truncate text-sm font-medium hover:text-primary"
                        >
                          {s.title}
                        </Link>
                        <span className="font-mono text-[11px] text-muted-foreground">
                          {formatDate(s.submitted_at)}
                        </span>
                      </div>
                      <StatusPill tone={statusTone(s.status)}>
                        <span className="text-[10px]">{s.status.replace(/_/g, " ")}</span>
                      </StatusPill>
                    </li>
                  ))}
                </ul>
              )}
            </div>

            <div className="rounded-lg border border-border bg-background p-5 shadow-card">
              <h3 className="text-base font-semibold tracking-tight">Grade distribution</h3>
              <p className="mt-1 text-xs text-muted-foreground">Letter grades across all checks</p>
              <div className="mt-4 space-y-3">
                {GRADES.map((g) => {
                  const c = gradeDist[g] ?? 0;
                  const pct = Math.round((c / gradeMax) * 100);
                  return (
                    <div key={g} className="flex items-center gap-3">
                      <span className="w-4 text-sm font-semibold" style={{ color: GRADE_COLOR[g] }}>
                        {g}
                      </span>
                      <div className="h-2 flex-1 rounded-full bg-muted">
                        <div
                          className="h-2 rounded-full"
                          style={{ width: `${pct}%`, background: GRADE_COLOR[g] }}
                        />
                      </div>
                      <span className="w-8 text-right font-mono text-xs text-muted-foreground">{c}</span>
                    </div>
                  );
                })}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
