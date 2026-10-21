"use client";
import * as React from "react";
import { DiffViewer } from "@/components/compare/DiffViewer";
import { sideLabel } from "@/lib/format";
import { applyFilterToBlocks } from "@/lib/compareModel";
import { useViewer } from "./ViewerContext";

/**
 * Text-mode / render-fallback view over the same engine records as the document view:
 * word highlighting follows the active filter (via each word's record id), and
 * selection maps record <-> diff block in both directions.
 */
export function TextRedline() {
  const { comparison, selectedChangeId, selectChange, showMoves, visibleIds, visibleChanges } = useViewer();
  const blocks = React.useMemo(
    () => applyFilterToBlocks(comparison.diff_result ?? [], visibleIds),
    [comparison.diff_result, visibleIds]
  );
  const selected = visibleChanges.find((c) => c.id === selectedChangeId);
  const selIndex = selected?.blockIndex;

  return (
    <div className="h-full overflow-auto p-3">
      <DiffViewer
        blocks={blocks}
        oldLabel={sideLabel(comparison, "old")}
        newLabel={sideLabel(comparison, "new")}
        selectedId={selIndex == null ? null : String(selIndex)}
        onSelect={(id) => {
          const bi = Number(id);
          const hit = visibleChanges.find((c) => c.blockIndex === bi);
          if (hit) selectChange(hit.id, "list");
        }}
        rowClassName={(block) =>
          showMoves && block.type !== "equal" && block.moved ? "border-l-2 border-l-[#04877E]" : undefined
        }
      />
    </div>
  );
}
