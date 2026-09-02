// frontend/components/compare-viewer/__tests__/deriveViewerChanges.test.ts
import { describe, expect, it } from "vitest";
import type { DocumentComparison } from "@/lib/types";
import { deriveViewerChanges } from "../ViewerContext";

function baseComparison(overrides: Partial<DocumentComparison> = {}): DocumentComparison {
  return {
    id: "c1",
    title: "t",
    old_content_type: "application/pdf",
    new_content_type: "application/pdf",
    status: "completed",
    created_at: "2026-01-01T00:00:00Z",
    ...overrides,
  };
}

describe("deriveViewerChanges", () => {
  it("pixel mode returns kind straight from render_result.changes regardless of showMoves", () => {
    const comparison = baseComparison({
      render_status: "completed",
      render_result: {
        old: { pages: [{ n: 1, w_pt: 612, h_pt: 792, boxes: [] }] },
        new: { pages: [{ n: 1, w_pt: 612, h_pt: 792, boxes: [] }] },
        truncated_pages: 0,
        changes: [
          { id: "r1", kind: "moved", old: { page: 1, bbox: [0, 0, 10, 10], text: "a" } },
        ],
      },
    });
    const withMoves = deriveViewerChanges(comparison, "pixel", true);
    const withoutMoves = deriveViewerChanges(comparison, "pixel", false);
    expect(withMoves[0].kind).toBe("moved");
    expect(withoutMoves[0].kind).toBe("moved");
  });

  it("text mode reclassifies moved blocks to removed/added/modified when showMoves is false", () => {
    const comparison = baseComparison({
      diff_result: [
        { type: "delete", old_text: "gone", moved: true, move_id: "m1" },
        { type: "insert", new_text: "new", moved: true, move_id: "m2" },
        {
          type: "replace",
          old_words: [{ text: "x", changed: true }],
          new_words: [{ text: "y", changed: true }],
          moved: true,
          move_id: "m3",
        },
      ],
    });
    const shown = deriveViewerChanges(comparison, "text", true);
    expect(shown.map((c) => c.kind)).toEqual(["moved", "moved", "moved"]);

    const hidden = deriveViewerChanges(comparison, "text", false);
    expect(hidden.map((c) => c.kind)).toEqual(["removed", "added", "modified"]);
  });

  it("skips equal blocks", () => {
    const comparison = baseComparison({
      diff_result: [
        { type: "equal", old_text: "same", new_text: "same" },
        { type: "delete", old_text: "gone" },
      ],
    });
    const changes = deriveViewerChanges(comparison, "text", true);
    expect(changes).toHaveLength(1);
    expect(changes[0].kind).toBe("removed");
  });

  it("fraction is monotonic with block index in text mode", () => {
    const comparison = baseComparison({
      diff_result: [
        { type: "delete", old_text: "a" },
        { type: "equal", old_text: "b", new_text: "b" },
        { type: "insert", new_text: "c" },
        { type: "delete", old_text: "d" },
      ],
    });
    const changes = deriveViewerChanges(comparison, "text", true);
    expect(changes.length).toBe(3);
    for (let i = 1; i < changes.length; i++) {
      expect(changes[i].fraction).toBeGreaterThan(changes[i - 1].fraction);
    }
  });
});
