import type { ReactNode } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { ScoreRing } from "@/components/ui/score-ring";
import { formatPercent, formatScore, gradeFromScore } from "@/lib/format";
import type { DashboardSummary } from "@/lib/types";

function Tile({ label, footnote, children }: { label: string; footnote?: string; children: ReactNode }) {
  return (
    <Card>
      <CardContent className="flex flex-col gap-3 p-5">
        <p className="micro-label">{label}</p>
        {children}
        {footnote && <p className="text-xs text-muted-foreground">{footnote}</p>}
      </CardContent>
    </Card>
  );
}

export function KpiCards({
  summary,
  criticalCount,
}: {
  summary: DashboardSummary;
  criticalCount: number;
}) {
  const avgScore = summary.avg_score ?? 0;
  const totalSubmissions = summary.total_submissions ?? 0;
  const thisWeek = summary.this_week ?? 0;
  const autoFixRate = summary.auto_fix_rate ?? 0;

  return (
    <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
      <Tile label="Total submissions" footnote={`+${thisWeek} this week`}>
        <p className="font-mono text-3xl font-semibold tabular-nums text-foreground">{totalSubmissions}</p>
      </Tile>

      <Tile label="Avg score">
        <div className="flex items-center gap-3">
          <ScoreRing value={avgScore} grade={gradeFromScore(avgScore)} size={52} />
          <p className="font-mono text-3xl font-semibold tabular-nums text-foreground">{formatScore(avgScore)}</p>
        </div>
      </Tile>

      <Tile label="Critical violations">
        <p className="font-mono text-3xl font-semibold tabular-nums text-sev-critical">{criticalCount}</p>
      </Tile>

      <Tile label="Auto-fix rate">
        <p className="font-mono text-3xl font-semibold tabular-nums text-foreground">
          {formatPercent(autoFixRate * 100)}
        </p>
      </Tile>
    </div>
  );
}
