import { getDashboardSummary, getViolationsByCategory, getViolationsBySeverity } from "@/lib/api";
import { KPICards } from "@/components/dashboard/KPICards";
import { CategoryRadar } from "@/components/dashboard/CategoryRadar";
import { SeverityHeatmap } from "@/components/dashboard/SeverityHeatmap";
import { Masthead, MetaItem } from "@/components/workspace/Masthead";

export const dynamic = "force-dynamic";

interface Summary {
  stats?: {
    total_submissions?: number;
    total_violations?: number;
    active_rules?: number;
    average_score?: number;
    total_checks?: number;
  };
  recent_submissions?: { id: string; title: string; status: string; submitted_at: string }[];
}

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

  const stats = summary?.stats ?? {};
  const hasData =
    (stats.total_submissions ?? 0) > 0 ||
    (stats.total_violations ?? 0) > 0 ||
    byCat.length > 0 ||
    bySev.length > 0;

  return (
    <div className="mx-auto max-w-6xl px-10 py-10">
      <Masthead
        edition="Insights · §03"
        title={<>Compliance <span className="italic">Dashboard</span></>}
        subtitle="Aggregate view of compliance health across submissions, with breakdowns by regulator and severity. Updated as analyses complete."
        meta={
          <>
            <MetaItem label="Submissions" value={stats.total_submissions ?? 0} />
            <MetaItem label="Violations" value={stats.total_violations ?? 0} />
            <MetaItem label="Avg score" value={(stats.average_score ?? 0).toFixed(1)} />
            <MetaItem label="Active rules" value={stats.active_rules ?? 0} />
          </>
        }
      />

      {err ? (
        <div className="rounded-md border border-border bg-surface p-8">
          <div className="micro-label text-sev-critical">API unreachable</div>
          <h2 className="mt-2 font-serif text-2xl">Dashboard data couldn&rsquo;t load.</h2>
          <p className="mt-2 text-sm text-muted-foreground">{err}</p>
        </div>
      ) : !hasData ? (
        <div className="rounded-md border border-border bg-surface p-12 text-center">
          <div className="micro-label mb-3">No analyses yet</div>
          <h2 className="font-serif text-2xl">Charts will appear after your first compliance check.</h2>
          <p className="mx-auto mt-3 max-w-md text-sm text-muted-foreground">
            The dashboard plots violations by regulator, severity distribution, and trend lines week-over-week.
          </p>
        </div>
      ) : (
        <>
          <KPICards stats={stats} />
          <div className="mt-8 grid gap-6 lg:grid-cols-2">
            <CategoryRadar rows={byCat} />
            <SeverityHeatmap rows={bySev} />
          </div>
        </>
      )}
    </div>
  );
}
