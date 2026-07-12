"use client";
import * as React from "react";
import { Download, Loader2 } from "lucide-react";
import { cn } from "@/lib/utils";
import { exportComparisonUrl, type ExportKind } from "@/lib/api";
import { Popover } from "@/components/ui/popover";
import { useViewer } from "./ViewerContext";

interface Row {
  kind: ExportKind;
  label: string;
  needsRender: boolean;
}

const ROWS: Row[] = [
  { kind: "changes-report.docx", label: "Changes Report (DOCX)", needsRender: false },
  { kind: "side-by-side.pdf", label: "Side by Side (PDF)", needsRender: true },
  { kind: "old-highlighted.pdf", label: "Original + highlights (PDF)", needsRender: true },
  { kind: "new-highlighted.pdf", label: "Revised + highlights (PDF)", needsRender: true },
  { kind: "bundle.zip", label: "Bundle (ZIP)", needsRender: false },
];

export function ExportPopover() {
  const { comparison } = useViewer();
  const renderReady = comparison.render_status === "completed";
  const [spinning, setSpinning] = React.useState<Record<string, boolean>>({});

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
        <div className="px-1.5 pb-1 micro-label">Comparison results</div>
        {ROWS.map((r) => {
          const disabled = r.needsRender && !renderReady;
          const busy = spinning[r.kind];
          if (disabled) {
            return (
              <div
                key={r.kind}
                title="Available once the document render completes"
                className="flex items-center gap-2 rounded-sm px-1.5 py-1.5 text-[12px] text-muted-foreground opacity-50"
              >
                <Download className="h-3.5 w-3.5" />
                {r.label}
              </div>
            );
          }
          return (
            <a
              key={r.kind}
              href={exportComparisonUrl(comparison.id, r.kind)}
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
