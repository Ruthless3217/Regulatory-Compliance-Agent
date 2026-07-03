// frontend/components/compare/CompareWorkspace.tsx
"use client";
import * as React from "react";
import { DiffViewer } from "./DiffViewer";
import { ChangesPane } from "./ChangesPane";
import { countDiffStats, deriveChanges } from "@/lib/format";
import type { DiffBlock } from "@/lib/types";

/**
 * Two-column Compare view: the diff on the left, a "Changes" sidebar on the
 * right. A shared `selectedId` (a changed block's index) syncs both directions —
 * clicking a change scrolls the diff to that block, and clicking a changed block
 * highlights its entry in the sidebar.
 */
export function CompareWorkspace({ blocks }: { blocks: DiffBlock[] }) {
  const [selectedId, setSelectedId] = React.useState<string | null>(null);
  const changes = React.useMemo(() => deriveChanges(blocks), [blocks]);
  const { removed, added } = React.useMemo(() => countDiffStats(blocks), [blocks]);

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[1fr_360px]">
      <DiffViewer blocks={blocks} selectedId={selectedId} onSelect={setSelectedId} />
      <ChangesPane
        changes={changes}
        removed={removed}
        added={added}
        selectedId={selectedId}
        onSelect={setSelectedId}
      />
    </div>
  );
}
