"use client";
import * as React from "react";
import { AlertTriangle } from "lucide-react";
import type { DocumentComparison } from "@/lib/types";
import { getComparison } from "@/lib/api";
import {
  ViewerProvider,
  useViewer,
  deriveViewerChanges,
  matchesViewerFilter,
} from "./ViewerContext";
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
  const {
    comparison,
    setComparison,
    effectiveMode,
    layoutMode,
    singleSide,
    showMoves,
    filter,
    annotationFor,
    selectedChangeId,
    setSelectedChangeId,
  } = useViewer();

  const isNoted = React.useCallback(
    (c: { id: string }) => {
      const a = annotationFor(c.id);
      return !!a && (!!a.note?.trim() || a.tags.length > 0);
    },
    [annotationFor]
  );

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
      const allChanges = deriveViewerChanges(comparison, effectiveMode, showMoves);
      const changes = allChanges.filter((c) => matchesViewerFilter(c, filter, isNoted(c)));
      if (changes.length === 0) return;
      e.preventDefault();
      const dir = e.key === "ArrowRight" ? 1 : -1;
      const cur = changes.findIndex((c) => c.id === selectedChangeId);
      const next =
        cur < 0
          ? dir === 1
            ? 0
            : changes.length - 1
          : (cur + dir + changes.length) % changes.length;
      setSelectedChangeId(changes[next].id);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [comparison, effectiveMode, showMoves, filter, isNoted, selectedChangeId, setSelectedChangeId]);

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
