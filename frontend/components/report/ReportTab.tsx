"use client";
import * as React from "react";
import { ScoreHero } from "./ScoreHero";
import { KPIStrip } from "./KPIStrip";
import { ViolationGroup } from "./ViolationGroup";
import { ExportPdfButton } from "./ExportPdfButton";
import { useSubmissionWorkspace } from "@/components/workspace/SubmissionWorkspaceContext";
import {
  AnalysisStateBanner,
  AnalysisWarningsBanner,
} from "@/components/review/AnalysisStateBanner";
import { normalizeSeverity } from "@/lib/format";

const SEVERITIES = ["critical", "high", "medium", "low"] as const;

export function ReportTab() {
  const {
    violations,
    overallScore,
    grade,
    submission,
    scores,
    analysisIncomplete,
    analysisMessage,
    analysisState,
    degradedReason,
    analysisWarnings,
  } = useSubmissionWorkspace();

  const {
    scoredViolations,
    needsReview,
    reviewerAdded,
    groups,
    reviewerGroups,
  } = React.useMemo(() => {
    const scored: typeof violations = [];
    const review: typeof violations = [];
    const added: typeof violations = [];
    const modelGroups: Record<string, typeof violations> = {
      critical: [], high: [], medium: [], low: [],
    };
    const humanGroups: Record<string, typeof violations> = {
      critical: [], high: [], medium: [], low: [],
    };

    for (const violation of violations) {
      const severity = normalizeSeverity(violation.severity);
      if (violation.source === "reviewer") {
        added.push(violation);
        humanGroups[severity].push(violation);
      } else if (violation.suppressed) {
        review.push(violation);
      } else {
        scored.push(violation);
        modelGroups[severity].push(violation);
      }
    }
    return {
      scoredViolations: scored,
      needsReview: review,
      reviewerAdded: added,
      groups: modelGroups,
      reviewerGroups: humanGroups,
    };
  }, [violations]);

  // Inject print stylesheet only on this route
  React.useEffect(() => {
    const id = "report-print-css";
    if (document.getElementById(id)) return;
    const style = document.createElement("style");
    style.id = id;
    style.media = "print";
    style.textContent = `
      @page { margin: 18mm; }
      body { background: white; color: #111; }
      aside, header { display: none !important; }
      main { padding-left: 0 !important; }
      .no-print { display: none !important; }
    `;
    document.head.appendChild(style);
    return () => { style.remove(); };
  }, []);

  return (
    <div className="h-full overflow-y-auto rounded-md border border-border bg-background pb-12">
      {/* One banner definition for both tabs, so the reason the reviewer reads
          on Review is the reason they read on the printed report. */}
      <AnalysisStateBanner
        analysisState={analysisState}
        analysisStatus={submission.status}
        analysisMessage={analysisMessage}
        degradedReason={degradedReason}
        className="mx-8 mt-6 rounded-md border"
      />
      {/* The score below is real, but it may not cover the whole document. */}
      <AnalysisWarningsBanner
        warnings={analysisWarnings}
        className="mx-8 mt-6 rounded-md border"
      />
      <ScoreHero score={overallScore} grade={grade} scores={scores} />
      <input type="hidden" data-submission-id={submission.id} />
      <KPIStrip violations={violations} />
      {needsReview.length > 0 && (
        <div className="mx-8 mt-5 rounded-md border border-sev-medium/40 bg-sev-medium/5 px-4 py-3 text-sm">
          <span className="font-medium">{needsReview.length} additional findings need human review.</span>
          <span className="ml-1 text-muted-foreground">
            They are persisted for audit but excluded from this score and the violation totals below.
          </span>
        </div>
      )}
      {reviewerAdded.length > 0 && (
        <div className="mx-8 mt-5 rounded-md border border-primary/30 bg-primary/5 px-4 py-3 text-sm">
          <span className="font-medium">{reviewerAdded.length} findings were added by reviewers after grading.</span>
          <span className="ml-1 text-muted-foreground">
            They remain auditable below but do not retroactively change this score.
          </span>
        </div>
      )}
      <div className="flex items-center justify-between px-8 py-4 no-print">
        <h2 className="font-serif text-lg">Scored violations</h2>
        <ExportPdfButton />
      </div>
      {scoredViolations.length === 0 ? (
        <div className="px-8 pb-12 text-center text-sm text-muted-foreground">
          {analysisIncomplete
            ? "No violations were recorded because the analysis did not complete — this is not a clean result."
            : needsReview.length > 0
              ? "No scored violations. Review the suppressed findings before treating the document as clean."
              : "No violations recorded for this submission."}
        </div>
      ) : (
        SEVERITIES.map((s) => (
          <ViolationGroup key={s} severity={s} violations={groups[s]} />
        ))
      )}
      {reviewerAdded.length > 0 && (
        <section className="mt-6 border-t border-border">
          <div className="px-8 py-4">
            <h2 className="font-serif text-lg">Reviewer-added findings</h2>
            <p className="mt-1 text-xs text-muted-foreground">
              Post-score human findings, reported separately from model-scored output.
            </p>
          </div>
          {SEVERITIES.map((s) => (
            <ViolationGroup key={"reviewer-" + s} severity={s} violations={reviewerGroups[s]} />
          ))}
        </section>
      )}
    </div>
  );
}
