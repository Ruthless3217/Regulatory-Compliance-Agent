"use client";
import { formatScore, gradeBand, gradeFromScore, categoryLabel } from "@/lib/format";
import { cn } from "@/lib/utils";

interface Props {
  score: number | null;
  grade: string | null;
  scores: Record<string, number> | null;
}

function bandClass(score: number | null | undefined) {
  const b = gradeBand(score);
  if (b === "success") return "border-success/40 bg-success/5 text-success";
  if (b === "info") return "border-primary/40 bg-primary-50 text-primary";
  if (b === "warning") return "border-sev-medium/40 bg-sev-medium/5 text-sev-medium";
  return "border-sev-critical/40 bg-sev-critical/5 text-sev-critical";
}

export function ScoreHero({ score, grade, scores }: Props) {
  const g = grade ?? (score !== null ? gradeFromScore(score) : "—");

  const categoryRows = scores
    ? Object.entries(scores).filter(([k]) => !["overall", "grade", "status"].includes(k))
    : [];

  return (
    <section className="border-b border-border bg-surface px-8 py-10">
      <div className="flex flex-wrap items-end gap-8">
        <div>
          <div className="micro-label mb-2">Overall</div>
          <div className="flex items-baseline gap-4">
            <span className="font-serif text-[120px] leading-none">{g}</span>
            <span className="font-mono text-3xl">{formatScore(score)}</span>
          </div>
        </div>
        <div className="flex flex-wrap gap-2 pb-2">
          {categoryRows.length === 0 ? (
            <span className="text-xs text-muted-foreground">No per-category scores yet.</span>
          ) : (
            categoryRows.map(([cat, s]) => (
              <span
                key={cat}
                className={cn(
                  "inline-flex items-center gap-2 rounded-sm border px-3 py-1 text-xs",
                  bandClass(s as number)
                )}
              >
                <span className="micro-label">{categoryLabel(cat)}</span>
                <span className="font-mono">{formatScore(s as number)}</span>
              </span>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
