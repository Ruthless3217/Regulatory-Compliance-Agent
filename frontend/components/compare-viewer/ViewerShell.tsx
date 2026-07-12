"use client";
import * as React from "react";
import { AlertTriangle } from "lucide-react";
import type { DocumentComparison } from "@/lib/types";
import { getComparison } from "@/lib/api";
import { ViewerProvider, useViewer } from "./ViewerContext";
import { Toolbar } from "./Toolbar";
import { PagePane } from "./PagePane";
import { TextRedline } from "./TextRedline";
import { HeatStrip } from "./HeatStrip";
import { ChangesPanel } from "./ChangesPanel";

export function ViewerShell({ comparison }: { comparison: DocumentComparison }) {
  return (
    <ViewerProvider comparison={comparison}>
      <ViewerShellInner />
    </ViewerProvider>
  );
}

function ViewerShellInner() {
  const { comparison, setComparison, effectiveMode, layoutMode, singleSide } = useViewer();

  // Poll while the pixel render is processing: 2 s, backing off to 5 s after
  // 60 s, and stopping on any terminal status.
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
      const interval = Date.now() - start > 60_000 ? 5000 : 2000;
      timer = setTimeout(tick, interval);
    };
    timer = setTimeout(tick, 2000);
    return () => {
      active = false;
      clearTimeout(timer);
    };
  }, [comparison.id, comparison.render_status, setComparison]);

  const renderFailed = comparison.render_status === "failed";

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

      <div
        className="grid min-h-0 flex-1"
        style={{ gridTemplateColumns: "minmax(0,1fr) 12px 380px" }}
      >
        <div className="min-h-0 overflow-hidden">
          {effectiveMode === "pixel" ? (
            layoutMode === "side-by-side" ? (
              <div className="grid h-full grid-cols-2">
                <PagePane side="old" />
                <PagePane side="new" className="border-l border-border" />
              </div>
            ) : (
              <PagePane side={singleSide} />
            )
          ) : (
            <TextRedline />
          )}
        </div>

        <HeatStrip />
        <ChangesPanel />
      </div>
    </div>
  );
}
