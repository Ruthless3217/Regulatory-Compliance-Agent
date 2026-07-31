"use client";
import { formatScore, gradeBand, gradeFromScore, categoryLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ScoreBreakdown } from "@/lib/types";

interface Props {
  score: number | null;
  grade: string | null;
  scores: ScoreBreakdown | null;
}

const RESERVED_SCORE_KEYS = new Set([
  "overall",
  "grade",
  "status",
  "weighted_burden",
  "scored_finding_count",
  "suppressed_finding_count",
  "scoring_policy_version",
]);

function bandClass(score: number | null | undefined) {
  const b = gradeBand(score);
  if (b === "success") return "border-success/40 bg-success/5 text-success";
  if (b === "info") return "border-primary/40 bg-primary-50 text-primary";
  if (b === "warning") return "border-sev-medium/40 bg-sev-medium/5 text-sev-medium";
  return "border-sev-critical/40 bg-sev-critical/5 text-sev-critical";
}

export function ScoreHero({ score, grade, scores }: Props) {
  const g = grade ?? (score !== null ? gradeFromScore(score) : "—");

  const categoryRows: [string, number][] = scores
    ? Object.entries(scores).flatMap(([key, value]) =>
        typeof value === "number" && !RESERVED_SCORE_KEYS.has(key)
          ? [[key, value] as [string, number]]
          : []
      )
    : [];
  const burden = typeof scores?.weighted_burden === "number" ? scores.weighted_burden : null;
  const scoredCount =
    typeof scores?.scored_finding_count === "number" ? scores.scored_finding_count : null;
  const suppressedCount =
    typeof scores?.suppressed_finding_count === "number"
      ? scores.suppressed_finding_count
      : null;
  const policy =
    typeof scores?.scoring_policy_version === "string"
      ? scores.scoring_policy_version
      : null;

  return (
    <section className="border-b border-border bg-background px-8 py-10">
      <div className="flex flex-wrap items-end gap-8">
        <div>
          <div className="micro-label mb-2">Overall</div>
          <div className="flex items-baseline gap-4">
            <span className="font-serif text-[120px] leading-none">{g}</span>
            <span className="font-mono text-3xl">{formatScore(score)}</span>
          </div>
          {(burden !== null || scoredCount !== null || suppressedCount !== null) && (
            <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              {burden !== null && <span>Weighted burden <b className="font-mono">{burden.toFixed(2)}</b></span>}
              {scoredCount !== null && <span>Scored findings <b className="font-mono">{scoredCount}</b></span>}
              {suppressedCount !== null && <span>Needs review <b className="font-mono">{suppressedCount}</b></span>}
              {policy && <span>Policy <b className="font-mono">{policy}</b></span>}
            </div>
          )}
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
                  bandClass(s)
                )}
              >
                <span className="micro-label">{categoryLabel(cat)}</span>
                <span className="font-mono">{formatScore(s)}</span>
              </span>
            ))
          )}
        </div>
      </div>
    </section>
  );
}
