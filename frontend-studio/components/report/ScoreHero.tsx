"use client";

import { AlertTriangle } from "lucide-react";
import { Card } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { ScoreRing } from "@/components/ui/score-ring";
import { formatScore } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { ComplianceResults } from "@/lib/types";

const CATEGORY_LABELS: Record<string, string> = {
  irdai: "IRDAI",
  sebi: "SEBI",
  brand: "Brand",
  regulatory: "Regulatory",
  seo: "SEO",
};

function toneForScore(score: number) {
  if (score >= 80) return { bar: "bg-success", text: "text-success" };
  if (score >= 60) return { bar: "bg-sev-medium", text: "text-sev-medium" };
  return { bar: "bg-sev-critical", text: "text-sev-critical" };
}

function toneForGrade(grade?: string | null) {
  if (grade === "A" || grade === "B") return "text-success";
  if (grade === "C" || grade === "D") return "text-sev-medium";
  if (grade === "F") return "text-sev-critical";
  return "text-muted-foreground";
}

function statusLabel(status?: string | null) {
  if (!status) return "Pending";
  return status.replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
}

function statusVariant(status?: string | null): "success" | "warning" | "secondary" {
  if (status === "compliant" || status === "approved") return "success";
  if (status === "requires_revision" || status === "non_compliant" || status === "rejected") return "warning";
  return "secondary";
}

/** Big score visual + per-category breakdown. Renders a "Needs review" banner
 * instead of a fake score when the result is degraded (no score / waiting for
 * review) rather than pretending a real grade was reached. */
export function ScoreHero({ result }: { result: ComplianceResults }) {
  const degraded = result.status === "waiting_for_review" || result.overall_score == null || !result.grade;

  if (degraded) {
    return (
      <Card className="border-sev-medium/40 bg-sev-medium/5">
        <div className="flex items-start gap-4 p-6">
          <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-sev-medium" />
          <div>
            <p className="font-medium text-sev-medium">Needs review</p>
            <p className="mt-1 text-sm text-muted-foreground">
              {result.message ??
                "This submission could not be fully scored. Send it for manual review before relying on this result."}
            </p>
          </div>
        </div>
      </Card>
    );
  }

  const categories = Object.entries(result.scores ?? {});

  return (
    <Card>
      <div className="flex flex-col gap-8 p-6 sm:flex-row sm:items-center">
        <div className="flex items-center gap-6">
          <ScoreRing value={result.overall_score ?? 0} size={128} />
          <div>
            <div className="micro-label mb-1">Grade</div>
            <div className={cn("font-mono text-6xl font-semibold leading-none", toneForGrade(result.grade))}>
              {result.grade}
            </div>
            <Badge variant={statusVariant(result.compliance_status)} className="mt-3">
              {statusLabel(result.compliance_status)}
            </Badge>
          </div>
        </div>

        <div className="hidden h-16 w-px bg-border sm:block" />
        <div className="h-px w-full bg-border sm:hidden" />

        <div className="grid flex-1 grid-cols-1 gap-3 sm:grid-cols-2">
          {categories.length === 0 ? (
            <p className="text-sm text-muted-foreground">No per-category scores yet.</p>
          ) : (
            categories.map(([cat, score]) => {
              const tone = toneForScore(score);
              return (
                <div key={cat} className="flex items-center gap-3">
                  <span className="micro-label w-20 shrink-0">{CATEGORY_LABELS[cat] ?? cat}</span>
                  <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted">
                    <div
                      className={cn("h-full rounded-full", tone.bar)}
                      style={{ width: `${Math.max(0, Math.min(100, score))}%` }}
                    />
                  </div>
                  <span className={cn("font-mono text-xs tabular-nums", tone.text)}>{formatScore(score)}</span>
                </div>
              );
            })
          )}
        </div>
      </div>
    </Card>
  );
}
