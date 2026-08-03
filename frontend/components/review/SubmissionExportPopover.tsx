"use client";
import * as React from "react";
import { Download, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { exportSubmissionUrl, type SubmissionExportKind } from "@/lib/api";
import { useSubmissionWorkspace } from "@/components/workspace/SubmissionWorkspaceContext";
import { Popover } from "@/components/ui/popover";

interface Row {
  kind: SubmissionExportKind;
  label: string;
}

// Mirrors submission_export_service.py's builder set — every kind is
// computable from the submission's current text at any time (unlike
// Compare's export, none of these need a completed pixel render), so there
// is no needsRender/disabled gating here.
const ROWS: Row[] = [
  { kind: "clean.docx", label: "Clean copy (DOCX)" },
  { kind: "clean.pdf", label: "Clean copy (PDF)" },
  { kind: "annotated.docx", label: "Annotated with highlights (DOCX)" },
  { kind: "annotated.pdf", label: "Annotated with highlights (PDF)" },
  { kind: "report.docx", label: "Findings report (DOCX)" },
  { kind: "report.pdf", label: "Findings report (PDF)" },
  { kind: "feedback-report.docx", label: "Reviewer feedback report (DOCX)" },
  { kind: "feedback-report.pdf", label: "Reviewer feedback report (PDF)" },
  { kind: "bundle.zip", label: "Everything (ZIP)" },
];

interface Props {
  submissionId: string;
}

export function SubmissionExportPopover({ submissionId }: Props) {
  const [spinning, setSpinning] = React.useState<Record<string, boolean>>({});
  const { findingsStale } = useSubmissionWorkspace();

  const spin = (kind: string) => {
    setSpinning((s) => ({ ...s, [kind]: true }));
    setTimeout(() => setSpinning((s) => ({ ...s, [kind]: false })), 2000);
  };

  return (
    <Popover
      align="start"
      className="w-64 p-2"
      trigger={({ open, toggle }) => (
        <button
          type="button"
          onClick={toggle}
          className={cn(
            "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 text-[11px] transition-colors",
            open
              ? "border-foreground bg-foreground text-background"
              : "border-border text-muted-foreground hover:border-foreground hover:text-foreground"
          )}
        >
          Export
        </button>
      )}
    >
      <div className="space-y-0.5">
        <div className="px-1.5 pb-1 micro-label">Submission exports</div>
        {findingsStale && (
          // The backend 409s these anyway; disabling them says why up front
          // instead of handing the reviewer a failed download.
          <div className="mb-1 rounded-sm bg-sev-high/10 px-1.5 py-1.5 text-[11px] text-muted-foreground">
            Re-run the compliance check to export — the document was edited after the
            last analysis.
          </div>
        )}
        {ROWS.map((r) => {
          const busy = spinning[r.kind];
          if (findingsStale) {
            return (
              <span
                key={r.kind}
                aria-disabled="true"
                className="flex cursor-not-allowed items-center gap-2 rounded-sm px-1.5 py-1.5 text-[12px] text-muted-foreground opacity-50"
              >
                <Download className="h-3.5 w-3.5" />
                {r.label}
              </span>
            );
          }
          return (
            <a
              key={r.kind}
              href={exportSubmissionUrl(submissionId, r.kind)}
              download
              onClick={() => spin(r.kind)}
              className="flex items-center gap-2 rounded-sm px-1.5 py-1.5 text-[12px] text-foreground transition-colors hover:bg-muted"
            >
              {busy ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : <Download className="h-3.5 w-3.5" />}
              {r.label}
            </a>
          );
        })}
      </div>
    </Popover>
  );
}
