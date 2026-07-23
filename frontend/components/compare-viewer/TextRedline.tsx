"use client";
import * as React from "react";
import { DiffViewer } from "@/components/compare/DiffViewer";
import { sideLabel } from "@/lib/format";
import { useViewer, textSelId, blockIndexFromSelId } from "./ViewerContext";

/**
 * Text-mode / render-fallback view. Wraps the existing DiffViewer, translating
 * between the viewer's "b{index}" selection ids and DiffViewer's String(index)
 * contract, and drawing a violet left border on moved rows when moves are on.
 */
export function TextRedline() {
  const { comparison, selectedChangeId, setSelectedChangeId, showMoves } = useViewer();
  const blocks = comparison.diff_result ?? [];
  const selIndex = blockIndexFromSelId(selectedChangeId);

  return (
    <div className="h-full overflow-auto p-3">
      <DiffViewer
        blocks={blocks}
        oldLabel={sideLabel(comparison, "old")}
        newLabel={sideLabel(comparison, "new")}
        selectedId={selIndex == null ? null : String(selIndex)}
        onSelect={(id) => setSelectedChangeId(textSelId(Number(id)))}
        rowClassName={(block) =>
          showMoves && block.type !== "equal" && block.moved
            ? "border-l-2 border-l-violet-600"
            : undefined
        }
      />
    </div>
  );
}
