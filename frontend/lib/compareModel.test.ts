import { describe, expect, it } from "vitest";
import {
  DEFAULT_GROUPS,
  applyFilterToBlocks,
  countByGroup,
  enabledIds,
  exportGroups,
  filterChanges,
  groupOf,
  modelFromBlocks,
  matchesBySidePage,
  modelFromRender,
  outlinePolygons,
  targetOn,
} from "./compareModel";
import type { DiffBlock, RenderResult } from "./types";

const page = (n: number) => ({ n, w_pt: 600, h_pt: 800, boxes: [] });

const rr: RenderResult = {
  old: { pages: [page(1), page(2)] },
  new: { pages: [page(1)] },
  truncated_pages: 0,
  changes: [
    { id: "r0", kind: "modified", filter_group: "replacements", default_visible: true,
      old: { page: 1, bbox: [10, 400, 50, 410], text: "Cell" }, new: { page: 1, bbox: [10, 400, 50, 410], text: "Call" } },
    { id: "r1", kind: "modified", filter_group: "case", default_visible: false, category: "typographic", subtype: "case",
      old: { page: 2, bbox: [10, 0, 50, 10], text: "Policy" }, new: { page: 1, bbox: [10, 700, 50, 710], text: "policy" } },
    { id: "r2", kind: "added", filter_group: "insertions", default_visible: true,
      old: { page: 1, bbox: [50, 400, 51, 410], text: "", anchor: true }, new: { page: 1, bbox: [60, 400, 120, 410], text: "within 30 days" } },
    { id: "r3", kind: "modified", filter_group: "placeholders", default_visible: false,
      old: { page: 1, bbox: [0, 0, 1, 1], text: "<xx>" }, new: { page: 1, bbox: [0, 0, 1, 1], text: "____" } },
  ],
};

describe("compare model — records from the render", () => {
  const changes = modelFromRender(rr);

  it("keeps every record, numbered in document order", () => {
    expect(changes.map((c) => [c.id, c.order])).toEqual([["r0", 1], ["r1", 2], ["r2", 3], ["r3", 4]]);
  });

  it("default view shows every record, like Draftable", () => {
    expect(filterChanges(changes, DEFAULT_GROUPS).map((c) => c.id)).toEqual(["r0", "r1", "r2", "r3"]);
    expect(DEFAULT_GROUPS.has("headers_footers")).toBe(true);
  });

  it("switching a group off hides its records", () => {
    const groups = new Set([...DEFAULT_GROUPS].filter((g) => g !== "case" && g !== "placeholders"));
    expect(filterChanges(changes, groups).map((c) => c.id)).toEqual(["r0", "r2"]);
  });

  it("counts per group", () => {
    const n = countByGroup(changes);
    expect([n.replacements, n.case, n.insertions, n.placeholders, n.quotes]).toEqual([1, 1, 1, 1, 0]);
  });

  it("insertion has an anchor target on the old side and a span on the new side", () => {
    const ins = changes.find((c) => c.id === "r2")!;
    expect(targetOn(ins, "old")).toMatchObject({ kind: "anchor", ref: { page: 1 } });
    expect(targetOn(ins, "new")).toMatchObject({ kind: "span" });
    expect(ins.removedText).toBeUndefined();
    expect(ins.addedText).toBe("within 30 days");
  });

  it("positions each side on its own lane (page index + y / page height)", () => {
    const r1 = changes.find((c) => c.id === "r1")!;
    expect(r1.oldPos!.top).toBeCloseTo(0.5, 5);        // old page 2 of 2, y=0
    expect(r1.newPos!.top).toBeCloseTo(700 / 800, 5);   // new page 1 of 1
  });

  it("records from older engines (no group) count as content", () => {
    expect(groupOf({ kind: "added" })).toBe("insertions");
    expect(groupOf({ kind: "moved" })).toBe("moves");
  });

  it("export groups are omitted for the default view", () => {
    expect(exportGroups(DEFAULT_GROUPS)).toBeUndefined();
    const noCase = exportGroups(new Set([...DEFAULT_GROUPS].filter((g) => g !== "case")));
    expect(noCase).toBeDefined();
    expect(noCase).not.toContain("case");
  });
});

describe("compare model — records from text blocks", () => {
  const blocks: DiffBlock[] = [
    { type: "equal", old_text: "Nomination may be cancelled.", new_text: "Nomination may be cancelled." },
    { type: "delete", old_text: "Removed entirely.", old_words: [
      { text: "Removed", changed: true, change_id: "r5", filter_group: "deletions" },
      { text: "entirely.", changed: true, change_id: "r5", filter_group: "deletions" }] },
    { type: "equal", old_text: "Grace Policy applies.", new_text: "Grace policy applies.",
      old_words: [{ text: "Grace", changed: false }, { text: "Policy", changed: false, change_id: "r6", filter_group: "case" }, { text: "applies.", changed: false }],
      new_words: [{ text: "Grace", changed: false }, { text: "policy", changed: false, change_id: "r6", filter_group: "case" }, { text: "applies.", changed: false }] },
    { type: "replace",
      old_words: [{ text: "Cell", changed: true, change_id: "r7", filter_group: "replacements" }, { text: "Centre", changed: false }],
      new_words: [{ text: "Call", changed: true, change_id: "r7", filter_group: "replacements" }, { text: "Centre", changed: false }] },
  ];

  it("rebuilds one change per record id, in block order", () => {
    const ch = modelFromBlocks(blocks);
    expect(ch.map((c) => [c.id, c.kind, c.group, c.blockIndex])).toEqual([
      ["r5", "removed", "deletions", 1],
      ["r6", "modified", "case", 2],
      ["r7", "modified", "replacements", 3],
    ]);
    expect(ch.find((c) => c.id === "r6")!.defaultVisible).toBe(true);
  });

  it("text view highlights exactly the enabled records", () => {
    const ch = modelFromBlocks(blocks);
    const noCase = applyFilterToBlocks(blocks, enabledIds(ch, new Set([...DEFAULT_GROUPS].filter((g) => g !== "case"))));
    expect(noCase[2].type).toBe("equal");
    const withCase = applyFilterToBlocks(blocks, enabledIds(ch, DEFAULT_GROUPS));
    expect(withCase[2].type).toBe("replace");
    const w = withCase[2] as Extract<DiffBlock, { type: "replace" }>;
    expect(w.old_words.filter((x) => x.changed).map((x) => x.text)).toEqual(["Policy"]);
    expect(withCase).toHaveLength(blocks.length); // indices preserved
  });

  it("switching a group off un-highlights its pure deletion rows", () => {
    const ch = modelFromBlocks(blocks);
    const noDel = applyFilterToBlocks(blocks, enabledIds(ch, new Set(["replacements" as const])));
    expect(noDel[1]).toMatchObject({ type: "equal", old_text: "Removed entirely.", new_text: "" });
  });

  it("legacy blocks without ids still produce changes", () => {
    const ch = modelFromBlocks([{ type: "insert", new_text: "Added." }]);
    expect(ch).toMatchObject([{ id: "b0", kind: "added", group: "insertions", defaultVisible: true }]);
  });
});

describe("compare model — matched-text outlines (Draftable hover/click units)", () => {
  it("one line is a rectangle", () => {
    expect(outlinePolygons([[10, 100, 200, 110]])).toEqual([[[10, 100], [200, 100], [200, 110], [10, 110]]]);
  });

  it("lines of one paragraph form one stair-shaped polygon hugging each line", () => {
    // first line starts mid-line (after a change), last line ends early
    const polys = outlinePolygons([
      [120, 100, 500, 110],
      [40, 114, 500, 124],
      [40, 128, 300, 138],
    ]);
    expect(polys).toHaveLength(1);
    const p = polys[0];
    // right edge goes down line by line, left edge comes back up
    expect(p[0]).toEqual([120, 100]);
    expect(p).toContainEqual([300, 138]);
    expect(p).toContainEqual([40, 138]);
    // the gap between lines is shared (no hole): line 1 bottom = line 2 top = 112
    expect(p).toContainEqual([500, 112]);
    expect(p).toContainEqual([40, 112]);
  });

  it("a column break or a big vertical gap starts a new polygon", () => {
    expect(outlinePolygons([[40, 100, 280, 110], [320, 100, 560, 110]])).toHaveLength(2);
    expect(outlinePolygons([[40, 100, 280, 110], [40, 300, 280, 310]])).toHaveLength(2);
  });

  it("indexes matches per side and page", () => {
    const m = matchesBySidePage([
      { id: "m0", old: [[1, 10, 10, 50, 20], [2, 10, 10, 50, 20]], new: [[1, 10, 10, 50, 20]] },
    ]);
    expect(m.old.get(2)?.map((x) => x.id)).toEqual(["m0"]);
    expect(m.new.get(1)?.[0].rects).toEqual([[10, 10, 50, 20]]);
  });
});
