"use client";
import * as React from "react";
import { AlertTriangle } from "lucide-react";
import type { DocumentComparison } from "@/lib/types";
import { getComparison } from "@/lib/api";
import { ViewerProvider, useViewer } from "./ViewerContext";
import { Toolbar } from "./Toolbar";
import { PagePane } from "./PagePane";
import { TextRedline } from "./TextRedline";
import { CenterChangeMap } from "./CenterChangeMap";
import { ChangesPanel } from "./ChangesPanel";
import { PreparingComparison } from "./PreparingComparison";

export function ViewerShell({ comparison }: { comparison: DocumentComparison }) {
  return (
    <ViewerProvider comparison={comparison}>
      <ViewerShellInner />
    </ViewerProvider>
  );
}

function ViewerShellInner() {
  const { comparison, setComparison, effectiveMode, layoutMode, singleSide, stepChange } = useViewer();

  // Left/Right arrow keys step through changes (same order as the Toolbar's
  // Prev/Next). Selecting a change scrolls it into view in both panes. We use
  // horizontal arrows so vertical arrow-scrolling of the document is preserved,
  // and we ignore keystrokes while a text field (e.g. per-pane search) is focused.
  React.useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
      if (e.metaKey || e.ctrlKey || e.altKey || e.shiftKey) return;
      const t = e.target as HTMLElement | null;
      if (
        t &&
        (t.tagName === "INPUT" ||
          t.tagName === "TEXTAREA" ||
          t.tagName === "SELECT" ||
          t.isContentEditable)
      ) {
        return;
      }
      e.preventDefault();
      stepChange(e.key === "ArrowRight" ? 1 : -1);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [stepChange]);

  // Poll while the pixel render is processing: 1 s (the loader shows live progress),
  // backing off to 3 s after 2 min, and stopping on any terminal status.
  React.useEffect(() => {
    if (comparison.render_status !== "processing") return;
    let active = true;
    const start = Date.now();
    let timer: ReturnType<typeof setTimeout>;
    const tick = async () => {
      try {
        const fresh = await getComparison(comparison.id);
        if (!active) return;
        setComparison(fresh);
        if (fresh.render_status !== "processing") return; // terminal → stop
      } catch {
        /* transient — keep polling */
      }
      if (!active) return;
      const interval = Date.now() - start > 120_000 ? 3000 : 1000;
      timer = setTimeout(tick, interval);
    };
    timer = setTimeout(tick, 1000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [comparison.id, comparison.render_status, setComparison]);

  const renderFailed = comparison.render_status === "failed";

  // Draftable: after upload, a "Preparing your comparison" card until the document
  // view is ready (then the viewer replaces it in place).
  if (comparison.render_status === "processing") {
    return <PreparingComparison progress={comparison.render_progress} />;
  }

  return (
    <div className="flex h-full w-full flex-col">
      <Toolbar />

      {renderFailed && (
        <div className="flex items-center gap-2 border-b border-sev-critical/30 bg-sev-critical/5 px-3 py-1.5 text-[11px] text-sev-critical">
          <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
          <span>
            Document view unavailable — showing text.
            {comparison.render_error ? ` (${comparison.render_error})` : ""}
          </span>
        </div>
      )}

      {effectiveMode === "pixel" && layoutMode === "side-by-side" ? (
        // Draftable layout: the change map sits between the two documents
        <div
          className="grid min-h-0 flex-1"
          style={{ gridTemplateColumns: "minmax(0,1fr) 44px minmax(0,1fr) 360px" }}
        >
          <PagePane side="old" className="min-h-0 overflow-hidden" />
          <CenterChangeMap />
          <PagePane side="new" className="min-h-0 overflow-hidden" />
          <ChangesPanel />
        </div>
      ) : (
        <div
          className="grid min-h-0 flex-1"
          style={{ gridTemplateColumns: "minmax(0,1fr) 44px 360px" }}
        >
          <div className="min-h-0 overflow-hidden">
            {effectiveMode === "pixel" ? <PagePane side={singleSide} /> : <TextRedline />}
          </div>
          <CenterChangeMap />
          <ChangesPanel />
        </div>
      )}
    </div>
  );
}
