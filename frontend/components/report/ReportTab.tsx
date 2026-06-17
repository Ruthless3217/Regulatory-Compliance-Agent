"use client";
import * as React from "react";
import { ScoreHero } from "./ScoreHero";
import { KPIStrip } from "./KPIStrip";
import { ViolationGroup } from "./ViolationGroup";
import { ExportPdfButton } from "./ExportPdfButton";
import { useSubmissionWorkspace } from "@/components/workspace/SubmissionWorkspaceContext";
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
  } = useSubmissionWorkspace();

  const groups = React.useMemo(() => {
    const m: Record<string, typeof violations> = { critical: [], high: [], medium: [], low: [] };
    for (const v of violations) m[normalizeSeverity(v.severity)].push(v);
    return m;
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
      {analysisIncomplete && (
        <div className="mx-8 mt-6 rounded-md border border-sev-medium/50 bg-sev-medium/5 px-4 py-3 text-sm text-sev-medium">
          <p className="font-medium">
            This document could not be fully analyzed — it has NOT been graded as
            compliant.
          </p>
          <p className="mt-1 text-xs">
            {analysisMessage ??
              "The analysis was incomplete or degraded. Re-run the check or send it for manual review before relying on this result."}
          </p>
        </div>
      )}
      <ScoreHero score={overallScore} grade={grade} scores={scores} />
      <input type="hidden" data-submission-id={submission.id} />
      <KPIStrip violations={violations} />
      <div className="flex items-center justify-between px-8 py-4 no-print">
        <h2 className="font-serif text-lg">Violations</h2>
        <ExportPdfButton />
      </div>
      {violations.length === 0 ? (
        <div className="px-8 pb-12 text-center text-sm text-muted-foreground">
          {analysisIncomplete
            ? "No violations were recorded because the analysis did not complete — this is not a clean result."
            : "No violations recorded for this submission."}
        </div>
      ) : (
        SEVERITIES.map((s) => (
          <ViolationGroup key={s} severity={s} violations={groups[s]} />
        ))
      )}
    </div>
  );
}
