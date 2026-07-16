"use client";
import * as React from "react";
import { DiffViewer } from "./DiffViewer";
import { PixelDiffViewer } from "./PixelDiffViewer";
import { ChangesPane } from "./ChangesPane";
import { countDiffStats, deriveChanges, sideLabel } from "@/lib/format";
import { getComparison } from "@/lib/api";
import type { DocumentComparison, ChangeItem } from "@/lib/types";

type ViewMode = "pixel" | "text";

export function CompareWorkspace({ comparison }: { comparison: DocumentComparison }) {
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const [live, setLive] = React.useState<DocumentComparison>(comparison);

  // Poll while the pixel render is still processing.
  React.useEffect(() => {
    if (live.render_status !== "processing") return;
    let active = true;
    const t = setInterval(async () => {
      try {
        const fresh = await getComparison(live.id);
        if (!active) return;
        setLive(fresh);
        if (fresh.render_status !== "processing") clearInterval(t);
      } catch { /* keep polling */ }
    }, 2000);
    return () => { active = false; clearInterval(t); };
  }, [live.id, live.render_status]);

  const hasPixel = live.render_status === "completed" && !!live.render_result;
  const [mode, setMode] = React.useState<ViewMode>("pixel");
  const effectiveMode: ViewMode = hasPixel && mode === "pixel" ? "pixel" : "text";

  const blocks = live.diff_result ?? [];
  const changes: ChangeItem[] = React.useMemo(() => {
    if (effectiveMode === "pixel" && live.render_result) {
      return live.render_result.changes.map((c) => ({
        id: c.id, blockIndex: 0, kind: c.kind,
        removedText: c.old?.text, addedText: c.new?.text,
      }));
    }
    return deriveChanges(blocks);
  }, [effectiveMode, live.render_result, blocks]);
  const { removed, added } = React.useMemo(() => countDiffStats(blocks), [blocks]);

  return (
    <div className="space-y-3">
      <ViewToggle
        mode={effectiveMode}
        pixelAvailable={hasPixel}
        pixelPending={live.render_status === "processing"}
        pixelFailed={live.render_status === "failed"}
        onChange={setMode}
      />
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_360px]">
        {effectiveMode === "pixel" && live.render_result ? (
          <PixelDiffViewer comparisonId={live.id} render={live.render_result}
            oldLabel={sideLabel(live, "old")} newLabel={sideLabel(live, "new")}
            selectedId={selectedId} onSelect={setSelectedId} />
        ) : (
          <DiffViewer blocks={blocks} oldLabel={sideLabel(live, "old")} newLabel={sideLabel(live, "new")}
            selectedId={selectedId} onSelect={setSelectedId} />
        )}
        <ChangesPane changes={changes} removed={removed} added={added}
          selectedId={selectedId} onSelect={setSelectedId} />
      </div>
    </div>
  );
}

function ViewToggle({
  mode, pixelAvailable, pixelPending, pixelFailed, onChange,
}: {
  mode: ViewMode; pixelAvailable: boolean; pixelPending: boolean;
  pixelFailed: boolean; onChange: (m: ViewMode) => void;
}) {
  return (
    <div className="flex items-center gap-2 text-[11px]">
      <button type="button" disabled={!pixelAvailable} onClick={() => onChange("pixel")}
        className={`rounded-sm border px-2 py-0.5 ${mode === "pixel" ? "border-foreground bg-foreground text-background" : "border-border text-muted-foreground"} ${!pixelAvailable ? "opacity-40" : ""}`}>
        Document view
      </button>
      <button type="button" onClick={() => onChange("text")}
        className={`rounded-sm border px-2 py-0.5 ${mode === "text" ? "border-foreground bg-foreground text-background" : "border-border text-muted-foreground"}`}>
        Text view
      </button>
      {pixelPending && <span className="text-muted-foreground">Rendering document view…</span>}
      {pixelFailed && <span className="text-sev-critical">Document view unavailable — showing text.</span>}
    </div>
  );
}
