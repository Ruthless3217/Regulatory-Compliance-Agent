"use client";
import * as React from "react";
import { Download, Loader2 } from "lucide-react";
import { toast } from "sonner";
import { cn } from "@/lib/utils";
import { exportSubmissionUrl, type SubmissionExportKind } from "@/lib/api";
import { useSubmissionWorkspace } from "@/components/workspace/SubmissionWorkspaceContext";
import { Popover } from "@/components/ui/popover";

interface Row {
  kind: SubmissionExportKind;
  label: string;
  /** True for the kinds that put the document and the findings in one file.
   * Only those are refused while the findings are stale — mirrors
   * submissions.py's `_STALE_BLOCKED_EXPORTS`, and the two lists must agree or
   * the popover greys out a download the backend would have served. */
  staleBlocked?: boolean;
}

// Mirrors submission_export_service.py's builder set — every kind is
// computable from the submission's current text at any time (unlike
// Compare's export, none of these need a completed pixel render).
const ROWS: Row[] = [
  { kind: "clean.docx", label: "Clean copy (DOCX)" },
  { kind: "clean.pdf", label: "Clean copy (PDF)" },
  { kind: "annotated.docx", label: "Annotated with highlights (DOCX)", staleBlocked: true },
  { kind: "annotated.pdf", label: "Annotated with highlights (PDF)", staleBlocked: true },
  { kind: "report.docx", label: "Findings report (DOCX)", staleBlocked: true },
  { kind: "report.pdf", label: "Findings report (PDF)", staleBlocked: true },
  { kind: "feedback-report.docx", label: "Reviewer feedback report (DOCX)" },
  { kind: "feedback-report.pdf", label: "Reviewer feedback report (PDF)" },
  { kind: "bundle.zip", label: "Everything (ZIP)", staleBlocked: true },
];

interface Props {
  submissionId: string;
}

export function SubmissionExportPopover({ submissionId }: Props) {
  const [busyKind, setBusyKind] = React.useState<string | null>(null);
  const { findingsStale } = useSubmissionWorkspace();

  /** Fetch the artifact, then save it from the blob.
   *
   * A plain `<a download>` navigates, so a 409/502 from the export route
   * became a browser error page or a downloaded JSON body — the reviewer saw
   * "export doesn't work" and nothing that said why. Fetching lets the
   * backend's own reason reach a toast, and the download still comes from the
   * same cookie-authenticated same-origin URL. */
  const download = async (kind: SubmissionExportKind, label: string) => {
    if (busyKind) return;
    setBusyKind(kind);
    try {
      const res = await fetch(exportSubmissionUrl(submissionId, kind), {
        credentials: "include",
        cache: "no-store",
      });
      if (!res.ok) {
        // FastAPI puts the readable reason in `detail`; fall back to the raw
        // body so a proxy error is not swallowed into a generic failure.
        const body = await res.text().catch(() => "");
        let reason = body;
        try {
          reason = (JSON.parse(body) as { detail?: string }).detail ?? body;
        } catch {
          /* not JSON — the body is the message */
        }
        throw new Error(reason || `${res.status} ${res.statusText}`);
      }
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      // Content-Disposition is set by the route; this is the fallback name the
      // blob URL needs since the browser cannot read the header from a blob.
      a.download = `${kind}`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      // Next tick: revoking synchronously cancels the download in Safari.
      setTimeout(() => URL.revokeObjectURL(url), 0);
    } catch (e) {
      toast.error(`${label} could not be exported: ${(e as Error).message}`);
    } finally {
      setBusyKind(null);
    }
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
          <div className="mb-1 rounded-sm bg-warning/10 px-1.5 py-1.5 text-[11px] leading-snug text-warning-fg">
            Document edited after the last analysis. The annotated copy and the findings
            reports would describe the previous version, so they need a re-run — the clean
            copy still downloads.
          </div>
        )}
        {ROWS.map((r) => {
          const blocked = findingsStale && r.staleBlocked;
          if (blocked) {
            return (
              <span
                key={r.kind}
                aria-disabled="true"
                title="Re-run the compliance check — this artifact pairs the document with its findings"
                className="flex cursor-not-allowed items-center gap-2 rounded-sm px-1.5 py-1.5 text-[12px] text-muted-foreground opacity-50"
              >
                <Download className="h-3.5 w-3.5" />
                {r.label}
              </span>
            );
          }
          return (
            <button
              key={r.kind}
              type="button"
              disabled={busyKind !== null}
              onClick={() => download(r.kind, r.label)}
              className="flex w-full items-center gap-2 rounded-sm px-1.5 py-1.5 text-left text-[12px] text-foreground transition-colors hover:bg-muted disabled:opacity-50"
            >
              {busyKind === r.kind ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : (
                <Download className="h-3.5 w-3.5" />
              )}
              {r.label}
            </button>
          );
        })}
      </div>
    </Popover>
  );
}
