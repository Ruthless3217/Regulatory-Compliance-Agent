/**
 * Compare viewer model — a pure view over the backend engine's change records.
 *
 * The content-first engine decides what changed and assigns every record a filter
 * group; this module never compares text. It only normalises records (from the
 * pixel render or from text blocks), filters them by the reviewer's enabled groups,
 * positions them for the dual-lane change map and resolves navigation targets,
 * including the anchor caret on the side of a one-sided change.
 */
import type {
  ChangeCategory,
  ChangeKind,
  DiffBlock,
  DiffWord,
  FilterGroup,
  RenderChange,
  RenderChangeRef,
  RenderMatch,
  RenderResult,
} from "./types";

export type Side = "old" | "new";
export type GroupSection = "content" | "typographic" | "template";

export const FILTER_GROUPS: { key: FilterGroup; label: string; section: GroupSection }[] = [
  { key: "insertions", label: "Insertions", section: "content" },
  { key: "deletions", label: "Deletions", section: "content" },
  { key: "replacements", label: "Replacements", section: "content" },
  { key: "moves", label: "Moves", section: "content" },
  { key: "currency", label: "Currency symbols", section: "content" },
  { key: "case", label: "Case", section: "typographic" },
  { key: "punctuation", label: "Punctuation", section: "typographic" },
  { key: "quotes", label: "Quote style", section: "typographic" },
  { key: "spacing", label: "Spacing", section: "typographic" },
  { key: "placeholders", label: "Placeholders & blanks", section: "template" },
  { key: "renumbering", label: "Renumbering", section: "template" },
  { key: "layout", label: "Repeated table headers", section: "template" },
  { key: "headers_footers", label: "Headers & footers", section: "template" },
];

export const SECTION_LABEL: Record<GroupSection, string> = {
  content: "Content changes",
  typographic: "Typographic only",
  template: "Template & structure",
};

/** Default view: every group, like Draftable (mirrors the backend's DEFAULT_GROUPS).
 *  Filters only switch categories off. */
export const DEFAULT_GROUPS: ReadonlySet<FilterGroup> = new Set<FilterGroup>(FILTER_GROUPS.map((g) => g.key));

/** Vertical position of a change on one side, as fractions of that document. */
export interface SidePos {
  top: number;
  height: number;
}

export interface ModelRef {
  page: number;
  bbox: [number, number, number, number];
  text: string;
  anchor: boolean;
  locations?: RenderChangeRef["locations"];
}

export interface ModelChange {
  id: string;
  /** stable 1-based number in document order across ALL records */
  order: number;
  kind: ChangeKind;
  group: FilterGroup;
  defaultVisible: boolean;
  category?: ChangeCategory;
  subtype?: string | null;
  changeType?: string;
  metadata?: Record<string, unknown>;
  structure?: RenderChange["structure"];
  removedText?: string;
  addedText?: string;
  moveId?: string;
  old?: ModelRef;
  new?: ModelRef;
  oldPos?: SidePos;
  newPos?: SidePos;
  /** text mode: first diff block that shows this change */
  blockIndex?: number;
}

/** Group for a record; records from older engines have none and are content. */
export function groupOf(c: { filter_group?: FilterGroup; kind: ChangeKind }): FilterGroup {
  if (c.filter_group) return c.filter_group;
  return c.kind === "added" ? "insertions" : c.kind === "removed" ? "deletions" : c.kind === "moved" ? "moves" : "replacements";
}

const isDefault = (g: FilterGroup, explicit?: boolean) => (explicit === undefined ? DEFAULT_GROUPS.has(g) : explicit);

function toRef(r?: RenderChangeRef): ModelRef | undefined {
  if (!r) return undefined;
  return { page: r.page, bbox: r.bbox, text: r.text ?? "", anchor: !!r.anchor, locations: r.locations };
}

function sidePos(rr: RenderResult, side: Side, ref?: ModelRef): SidePos | undefined {
  if (!ref) return undefined;
  const pages = rr[side].pages;
  const n = pages.length || 1;
  const at = (page: number, y: number) => {
    const idx = pages.findIndex((p) => p.n === page);
    const i = idx >= 0 ? idx : Math.max(0, page - 1);
    const h = (idx >= 0 ? pages[idx].h_pt : 0) || 1;
    return Math.min(1, Math.max(0, (i + y / h) / n));
  };
  const locs = ref.locations && ref.locations.length ? ref.locations : [{ page: ref.page, bbox: ref.bbox }];
  const top = at(locs[0].page, locs[0].bbox[1]);
  const last = locs[locs.length - 1];
  const bottom = at(last.page, last.bbox[3]);
  return { top, height: Math.max(0, bottom - top) };
}

/** Records of a completed pixel render, in document order. */
export function modelFromRender(rr: RenderResult): ModelChange[] {
  return rr.changes.map((c, i) => {
    const group = groupOf(c);
    const old = toRef(c.old);
    const nw = toRef(c.new);
    return {
      id: c.id,
      order: i + 1,
      kind: c.kind,
      group,
      defaultVisible: isDefault(group, c.default_visible),
      category: c.category,
      subtype: c.subtype,
      changeType: c.change_type,
      metadata: c.metadata,
      structure: c.structure,
      removedText: old && !old.anchor ? old.text : undefined,
      addedText: nw && !nw.anchor ? nw.text : undefined,
      moveId: c.move_id,
      old,
      new: nw,
      oldPos: sidePos(rr, "old", old),
      newPos: sidePos(rr, "new", nw),
    };
  });
}

type WordAcc = {
  id: string;
  group: FilterGroup;
  category?: ChangeCategory;
  subtype?: string | null;
  oldWords: string[];
  newWords: string[];
  firstBlock: number;
  moved: boolean;
  moveId?: string;
};

/** Records of a text-only comparison, rebuilt from the word-level record ids. Blocks
 *  from older engines (no ids) fall back to one change per non-equal block. */
export function modelFromBlocks(blocks: DiffBlock[], showMoves = true): ModelChange[] {
  const total = Math.max(1, blocks.length);
  const acc = new Map<string, WordAcc>();
  const order: string[] = [];
  const touch = (w: DiffWord, side: Side, bi: number) => {
    if (!w.change_id) return;
    let a = acc.get(w.change_id);
    if (!a) {
      a = { id: w.change_id, group: w.filter_group ?? "replacements", category: w.category, subtype: w.subtype,
            oldWords: [], newWords: [], firstBlock: bi, moved: false };
      acc.set(w.change_id, a);
      order.push(w.change_id);
    }
    (side === "old" ? a.oldWords : a.newWords).push(w.text);
  };

  const legacy: ModelChange[] = [];
  blocks.forEach((b, bi) => {
    if ((b.type === "delete" || b.type === "insert") && b.moved && b.change_id) {
      let a = acc.get(b.change_id);
      if (!a) {
        a = { id: b.change_id, group: "moves", oldWords: [], newWords: [], firstBlock: bi, moved: true, moveId: b.move_id };
        acc.set(b.change_id, a);
        order.push(b.change_id);
      }
      (b.type === "delete" ? a.oldWords : a.newWords).push(b.type === "delete" ? b.old_text : b.new_text);
      return;
    }
    const ow = "old_words" in b ? b.old_words : undefined;
    const nw = "new_words" in b ? b.new_words : undefined;
    const hasIds = !!(ow?.some((w) => w.change_id) || nw?.some((w) => w.change_id));
    if (hasIds) {
      ow?.forEach((w) => touch(w, "old", bi));
      nw?.forEach((w) => touch(w, "new", bi));
      return;
    }
    if (b.type === "equal") return;
    // legacy block without record ids
    const moved = showMoves && !!b.moved;
    const kind: ChangeKind = moved ? "moved" : b.type === "delete" ? "removed" : b.type === "insert" ? "added" : "modified";
    legacy.push({
      id: `b${bi}`,
      order: 0,
      kind,
      group: groupOf({ kind }),
      defaultVisible: true,
      removedText: b.type === "delete" ? b.old_text : b.type === "replace" ? b.old_words.filter((w) => w.changed).map((w) => w.text).join(" ") : undefined,
      addedText: b.type === "insert" ? b.new_text : b.type === "replace" ? b.new_words.filter((w) => w.changed).map((w) => w.text).join(" ") : undefined,
      moveId: b.move_id,
      blockIndex: bi,
      oldPos: { top: bi / total, height: 1 / total },
      newPos: { top: bi / total, height: 1 / total },
    });
  });

  const fromIds: ModelChange[] = order.map((id) => {
    const a = acc.get(id)!;
    const kind: ChangeKind = a.moved ? "moved" : a.oldWords.length && a.newWords.length ? "modified" : a.oldWords.length ? "removed" : "added";
    const pos = { top: a.firstBlock / total, height: 1 / total };
    return {
      id,
      order: 0,
      kind,
      group: a.group,
      defaultVisible: DEFAULT_GROUPS.has(a.group),
      category: a.category,
      subtype: a.subtype,
      changeType: kind === "added" ? "insertion" : kind === "removed" ? "deletion" : kind === "moved" ? "reordered" : "replacement",
      removedText: a.oldWords.join(" ") || undefined,
      addedText: a.newWords.join(" ") || undefined,
      moveId: a.moveId,
      blockIndex: a.firstBlock,
      oldPos: a.oldWords.length ? pos : undefined,
      newPos: a.newWords.length ? pos : undefined,
    };
  });

  return [...fromIds, ...legacy]
    .sort((x, y) => (x.blockIndex ?? 0) - (y.blockIndex ?? 0))
    .map((c, i) => ({ ...c, order: i + 1 }));
}

export function filterChanges(changes: ModelChange[], enabled: ReadonlySet<FilterGroup>): ModelChange[] {
  return changes.filter((c) => enabled.has(c.group));
}

export function countByGroup(changes: ModelChange[]): Record<FilterGroup, number> {
  const out = Object.fromEntries(FILTER_GROUPS.map((g) => [g.key, 0])) as Record<FilterGroup, number>;
  for (const c of changes) out[c.group] = (out[c.group] ?? 0) + 1;
  return out;
}

export function enabledIds(changes: ModelChange[], enabled: ReadonlySet<FilterGroup>): Set<string> {
  return new Set(filterChanges(changes, enabled).map((c) => c.id));
}

const sameSet = (a: ReadonlySet<string>, b: ReadonlySet<string>) => a.size === b.size && [...a].every((x) => b.has(x));

/** Groups to send with an export request; undefined when the reviewer is on the default view. */
export function exportGroups(enabled: ReadonlySet<FilterGroup>): FilterGroup[] | undefined {
  return sameSet(enabled, DEFAULT_GROUPS) ? undefined : [...enabled];
}

/** The navigation target in one pane: the change's own text, or its anchor caret. */
export function targetOn(change: ModelChange, side: Side): { kind: "span" | "anchor"; ref: ModelRef } | null {
  const ref = side === "old" ? change.old : change.new;
  if (!ref) return null;
  return { kind: ref.anchor ? "anchor" : "span", ref };
}

/** Text-view blocks with word highlighting recomputed for the enabled records. Indices
 *  are preserved 1:1 so block-based selection keeps working. */
export function applyFilterToBlocks(blocks: DiffBlock[], ids: ReadonlySet<string>): DiffBlock[] {
  const on = (w: DiffWord) => (w.change_id ? ids.has(w.change_id) : w.changed);
  return blocks.map((b) => {
    if (b.type === "replace" || (b.type === "equal" && (b.old_words || b.new_words))) {
      const old_words = (b.old_words ?? []).map((w) => ({ ...w, changed: on(w) }));
      const new_words = (b.new_words ?? []).map((w) => ({ ...w, changed: on(w) }));
      if (!old_words.some((w) => w.changed) && !new_words.some((w) => w.changed)) {
        return {
          type: "equal",
          old_text: old_words.map((w) => w.text).join(" "),
          new_text: new_words.map((w) => w.text).join(" "),
          old_words,
          new_words,
        } as DiffBlock;
      }
      return { type: "replace", old_words, new_words, moved: "moved" in b ? b.moved : undefined } as DiffBlock;
    }
    // a pure deletion/insertion whose record group is switched off reads as plain text
    if (b.type === "delete" && !b.moved && b.old_words?.length && !b.old_words.some(on)) {
      return { type: "equal", old_text: b.old_text, new_text: "" } as DiffBlock;
    }
    if (b.type === "insert" && !b.moved && b.new_words?.length && !b.new_words.some(on)) {
      return { type: "equal", old_text: "", new_text: b.new_text } as DiffBlock;
    }
    return b;
  });
}

/* ------------------------------------------------------------------ *
 * Matched text (Draftable's hover/click units): unchanged sentences linked across the
 * documents. The outline hugs the text line by line, like Draftable's polygon.
 * ------------------------------------------------------------------ */
export type Rect = [number, number, number, number];
export type Point = [number, number];

export interface SideMatch {
  id: string;
  rects: Rect[];
}

/** Matches indexed per side and page: side -> page -> matches with that page's rects. */
export function matchesBySidePage(matches: RenderMatch[] | undefined): Record<Side, Map<number, SideMatch[]>> {
  const out: Record<Side, Map<number, SideMatch[]>> = { old: new Map(), new: new Map() };
  for (const m of matches ?? []) {
    for (const side of ["old", "new"] as const) {
      const byPage = new Map<number, Rect[]>();
      for (const [page, x0, y0, x1, y1] of m[side] ?? []) {
        const list = byPage.get(page) ?? [];
        list.push([x0, y0, x1, y1]);
        byPage.set(page, list);
      }
      byPage.forEach((rects, page) => {
        const list = out[side].get(page) ?? [];
        list.push({ id: m.id, rects });
        out[side].set(page, list);
      });
    }
  }
  return out;
}

/** Line rectangles of one unit on one page -> outline polygons. Consecutive lines that
 *  overlap horizontally form one stair-shaped polygon whose edges meet halfway between
 *  lines; a column break or a paragraph-sized gap starts another polygon. */
export function outlinePolygons(rects: Rect[]): Point[][] {
  const sorted = [...rects].sort((a, b) => a[1] - b[1] || a[0] - b[0]);
  const clusters: Rect[][] = [];
  for (const r of sorted) {
    const cl = clusters[clusters.length - 1];
    const last = cl?.[cl.length - 1];
    if (last) {
      const h = Math.max(last[3] - last[1], r[3] - r[1], 1);
      const sameLine = Math.min(last[3], r[3]) - Math.max(last[1], r[1]) > 0.5 * Math.min(last[3] - last[1], r[3] - r[1]);
      if (sameLine && r[0] - last[2] <= 2 * h) {
        cl[cl.length - 1] = [Math.min(last[0], r[0]), Math.min(last[1], r[1]), Math.max(last[2], r[2]), Math.max(last[3], r[3])];
        continue;
      }
      const overlapX = Math.min(last[2], r[2]) - Math.max(last[0], r[0]) > 0;
      if (!sameLine && r[1] - last[3] <= 0.8 * h && overlapX) {
        cl.push(r);
        continue;
      }
    }
    clusters.push([r]);
  }
  return clusters.map((ls) => {
    const top = ls.map((l, i) => (i === 0 ? l[1] : (ls[i - 1][3] + l[1]) / 2));
    const bot = ls.map((l, i) => (i === ls.length - 1 ? l[3] : (l[3] + ls[i + 1][1]) / 2));
    const pts: Point[] = [[ls[0][0], top[0]]];
    ls.forEach((l, i) => pts.push([l[2], top[i]], [l[2], bot[i]]));
    for (let i = ls.length - 1; i >= 0; i--) pts.push([ls[i][0], bot[i]], [ls[i][0], top[i]]);
    pts.pop(); // closes on the first point
    return pts.filter((p, i) => i === 0 || p[0] !== pts[i - 1][0] || p[1] !== pts[i - 1][1]);
  });
}
