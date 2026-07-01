import { ShieldCheck } from "lucide-react";
import { gradeFromScore } from "@/lib/format";

interface Props {
 score: number;
 totalChecks: number;
 submissionsThisWeek: number;
}

/**
 * Bold Bajaj-blue hero card showing the average compliance score + grade.
 * The single saturated brand-color surface that anchors the dashboard.
 */
export function ScoreHeroCard({ score, totalChecks, submissionsThisWeek }: Props) {
 const grade = gradeFromScore(score);
 const pct = Math.min(100, Math.max(0, score));
 return (
 <div className="flex flex-col justify-between rounded-lg bg-primary p-6 text-primary-foreground shadow-card">
 <div className="flex items-center justify-between">
 <span className="text-[11px] font-medium uppercase tracking-wide text-primary-foreground/70">
 Avg compliance score
 </span>
 <ShieldCheck className="h-4 w-4 text-primary-foreground/70" />
 </div>
 <div className="mt-5 flex items-end gap-3">
 <span className="font-mono text-[44px] leading-none">{score.toFixed(1)}</span>
 <span className="mb-1 rounded-md bg-white/15 px-2 py-0.5 text-sm font-semibold">
 Grade {grade}
 </span>
 </div>
 <div className="mt-5">
 <div className="h-1.5 w-full overflow-hidden rounded-full bg-white/20">
 <div className="h-full rounded-full bg-white/90" style={{ width: `${pct}%` }} />
 </div>
 <p className="mt-2.5 text-xs text-primary-foreground/70">
 Across {totalChecks} checks · {submissionsThisWeek} new this week
 </p>
 </div>
 </div>
 );
}
