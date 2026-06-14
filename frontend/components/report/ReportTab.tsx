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
  const { violations, overallScore, grade, submission } = useSubmissionWorkspace();

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
      <ScoreHero score={overallScore} grade={grade} scores={null} />
      <input type="hidden" data-submission-id={submission.id} />
      <KPIStrip violations={violations} />
      <div className="flex items-center justify-between px-8 py-4 no-print">
        <h2 className="font-serif text-lg">Violations</h2>
        <ExportPdfButton />
      </div>
      {violations.length === 0 ? (
        <div className="px-8 pb-12 text-center text-sm text-muted-foreground">
          No violations recorded for this submission.
        </div>
      ) : (
        SEVERITIES.map((s) => (
          <ViolationGroup key={s} severity={s} violations={groups[s]} />
        ))
      )}
    </div>
  );
}
