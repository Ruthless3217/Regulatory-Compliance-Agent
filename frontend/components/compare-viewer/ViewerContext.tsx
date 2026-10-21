"use client";
import * as React from "react";
import type { Annotation, DocumentComparison, FilterGroup } from "@/lib/types";
import {
  DEFAULT_GROUPS,
  filterChanges,
  modelFromBlocks,
  modelFromRender,
  type ModelChange,
} from "@/lib/compareModel";

export type ViewMode = "pixel" | "text";
export type LayoutMode = "side-by-side" | "single";
export type Side = "old" | "new";
export type CursorMode = "scroll" | "select";
/** Where a selection came from — decides which lane marker is "selected" vs "linked". */
export type SelectionSource = Side | "list" | "map" | "keyboard";

/** A change as the viewer shows it: one backend engine record. */
export type ViewerChange = ModelChange;

export const ZOOM_MIN = 0.5;
export const ZOOM_MAX = 3;

/** Draftable-style markup palette (fill / marker), switchable per change type. */
export type MarkupColor = "red" | "blue" | "green";
export interface MarkupColors {
  deletions: MarkupColor;
  insertions: MarkupColor;
  moves: MarkupColor;
}
export const DEFAULT_MARKUP: MarkupColors = { deletions: "red", insertions: "blue", moves: "green" };
export const MARKUP_SWATCH: Record<MarkupColor, { fill: string; marker: string; text: string }> = {
  red: { fill: "#FFC9CB", marker: "#FF8185", text: "#D13438" },
  blue: { fill: "#C0D8EF", marker: "#94B9FF", text: "#0563C1" },
  green: { fill: "#D6EFE8", marker: "#7FD1B9", text: "#04877E" },
};

/* ------------------------------------------------------------------ *
 * Shared scroll registry. Panes register their scroll containers so scroll
 * lock can sync the sibling pane and the change map can follow the viewport.
 * `hold(ms)` suspends lock-sync while the viewer itself scrolls both panes to a
 * selected change, so the two smooth scrolls don't fight each other.
 * ------------------------------------------------------------------ */
type ScrollSubscriber = (fraction: number, side: Side) => void;

export interface ScrollRegistry {
  register: (side: Side, el: HTMLDivElement | null) => void;
  get: (side: Side) => HTMLDivElement | null;
  handleScroll: (side: Side, scrollLock: boolean) => void;
  scrollToFraction: (side: Side, fraction: number) => void;
  subscribe: (cb: ScrollSubscriber) => () => void;
  hold: (ms: number) => void;
}

export function createScrollRegistry(): ScrollRegistry {
  const els: Record<Side, HTMLDivElement | null> = { old: null, new: null };
  const subs = new Set<ScrollSubscriber>();
  let syncing = false;
  let heldUntil = 0;
  return {
    register(side, el) {
      els[side] = el;
    },
    get(side) {
      return els[side];
    },
    subscribe(cb) {
      subs.add(cb);
      return () => {
        subs.delete(cb);
      };
    },
    hold(ms) {
      heldUntil = Date.now() + ms;
    },
    handleScroll(side, scrollLock) {
      const el = els[side];
      if (!el) return;
      const max = el.scrollHeight - el.clientHeight;
      const frac = max > 0 ? el.scrollTop / max : 0;
      subs.forEach((cb) => cb(frac, side));
      const now = Date.now();
      if (now < heldUntil) {
        // a navigation scroll is still animating: keep holding until the panes settle
        heldUntil = Math.max(heldUntil, now + 300);
        return;
      }
      if (syncing || !scrollLock) return;
      const other: Side = side === "old" ? "new" : "old";
      const oel = els[other];
      if (!oel) return;
      syncing = true;
      const omax = oel.scrollHeight - oel.clientHeight;
      oel.scrollTop = frac * omax;
      requestAnimationFrame(() => {
        syncing = false;
      });
    },
    scrollToFraction(side, fraction) {
      const el = els[side];
      if (!el) return;
      const max = el.scrollHeight - el.clientHeight;
      el.scrollTo({ top: fraction * max, behavior: "smooth" });
    },
  };
}

/* ------------------------------------------------------------------ *
 * Hover target shared by both panes (Draftable: hovering text or a change outlines it
 * AND its counterpart on the other document). A tiny external store so a hover only
 * re-renders the outline layers, never the page tiles.
 * ------------------------------------------------------------------ */
export type HoverTarget = { kind: "match" | "change"; id: string } | null;

export interface HoverStore {
  get: () => HoverTarget;
  set: (t: HoverTarget) => void;
  /** clear only if `id` is still the hovered target (mouse-leave races mouse-enter) */
  clear: (id: string) => void;
  subscribe: (cb: () => void) => () => void;
}

export function createHoverStore(): HoverStore {
  let cur: HoverTarget = null;
  const subs = new Set<() => void>();
  const emit = () => subs.forEach((cb) => cb());
  return {
    get: () => cur,
    set(t) {
      if (cur?.kind === t?.kind && cur?.id === t?.id) return;
      cur = t;
      emit();
    },
    clear(id) {
      if (cur?.id !== id) return;
      cur = null;
      emit();
    },
    subscribe(cb) {
      subs.add(cb);
      return () => {
        subs.delete(cb);
      };
    },
  };
}

export function useHoverTarget(store: HoverStore): HoverTarget {
  return React.useSyncExternalStore(store.subscribe, store.get, store.get);
}

/** All engine records for the current view mode (pixel render or text blocks). */
export function deriveViewerChanges(comparison: DocumentComparison, mode: ViewMode, showMoves = true): ViewerChange[] {
  if (mode === "pixel" && comparison.render_result) return modelFromRender(comparison.render_result);
  return modelFromBlocks(comparison.diff_result ?? [], showMoves);
}

/* ------------------------------------------------------------------ */

interface ViewerState {
  comparison: DocumentComparison;
  setComparison: React.Dispatch<React.SetStateAction<DocumentComparison>>;

  /** every record (hidden ones included) and the ones the filter shows */
  allChanges: ViewerChange[];
  visibleChanges: ViewerChange[];
  visibleIds: ReadonlySet<string>;

  groups: ReadonlySet<FilterGroup>;
  toggleGroup: (g: FilterGroup) => void;
  setGroups: (g: ReadonlySet<FilterGroup>) => void;
  resetGroups: () => void;
  notedOnly: boolean;
  setNotedOnly: (v: boolean) => void;

  selectedChangeId: string | null;
  selectionSource: SelectionSource | null;
  /** increments on every selection — even re-clicking the same change — so panes
   *  re-scroll to it and replay the highlight blink */
  selectionNonce: number;
  selectChange: (id: string | null, source?: SelectionSource) => void;
  /** clicked unchanged text (a matched unit): outlined on both sides, both panes aligned */
  selectedMatchId: string | null;
  selectMatch: (id: string | null, source?: SelectionSource) => void;
  hover: HoverStore;
  /** @deprecated use selectChange */
  setSelectedChangeId: (id: string | null) => void;
  stepChange: (dir: 1 | -1 | "first" | "last") => void;

  viewMode: ViewMode;
  setViewMode: (m: ViewMode) => void;
  hasPixel: boolean;
  effectiveMode: ViewMode;

  layoutMode: LayoutMode;
  setLayoutMode: (m: LayoutMode) => void;
  singleSide: Side;
  setSingleSide: (s: Side) => void;

  zoom: Record<Side, number>;
  setZoom: (side: Side, value: number) => void;

  scrollLock: boolean;
  setScrollLock: (v: boolean) => void;
  cursorMode: CursorMode;
  setCursorMode: (m: CursorMode) => void;
  showMoves: boolean;
  setShowMoves: (v: boolean) => void;

  markup: MarkupColors;
  setMarkup: (m: MarkupColors) => void;

  scroll: ScrollRegistry;

  annotationFor: (changeId: string) => Annotation | undefined;
  setAnnotationLocal: (a: Annotation) => void;
  removeAnnotationLocal: (changeId: string) => void;
}

const ViewerCtx = React.createContext<ViewerState | null>(null);

export function ViewerProvider({
  comparison: initial,
  children,
}: {
  comparison: DocumentComparison;
  children: React.ReactNode;
}) {
  const [comparison, setComparison] = React.useState<DocumentComparison>(initial);
  const [selectedChangeId, setSelected] = React.useState<string | null>(null);
  const [selectionSource, setSelectionSource] = React.useState<SelectionSource | null>(null);
  const [selectionNonce, setSelectionNonce] = React.useState(0);
  const [selectedMatchId, setSelectedMatch] = React.useState<string | null>(null);
  const hoverRef = React.useRef<HoverStore | null>(null);
  if (hoverRef.current === null) hoverRef.current = createHoverStore();
  const hover = hoverRef.current;
  const [viewMode, setViewMode] = React.useState<ViewMode>("pixel");
  const [layoutMode, setLayoutMode] = React.useState<LayoutMode>("side-by-side");
  const [singleSide, setSingleSide] = React.useState<Side>("new");
  const [zoom, setZoomState] = React.useState<Record<Side, number>>({ old: 1, new: 1 });
  const [scrollLock, setScrollLock] = React.useState(true);
  const [cursorMode, setCursorMode] = React.useState<CursorMode>("scroll");
  const [showMoves, setShowMoves] = React.useState(true);
  const [groups, setGroupsState] = React.useState<ReadonlySet<FilterGroup>>(DEFAULT_GROUPS);
  const [notedOnly, setNotedOnly] = React.useState(false);
  const [markup, setMarkup] = React.useState<MarkupColors>(DEFAULT_MARKUP);

  const scrollRef = React.useRef<ScrollRegistry | null>(null);
  if (scrollRef.current === null) scrollRef.current = createScrollRegistry();
  const scroll = scrollRef.current;

  const hasPixel = comparison.render_status === "completed" && !!comparison.render_result;
  const effectiveMode: ViewMode = hasPixel && viewMode === "pixel" ? "pixel" : "text";

  const annotationFor = React.useCallback(
    (changeId: string) => comparison.annotations?.find((a) => a.change_id === changeId),
    [comparison.annotations]
  );

  const allChanges = React.useMemo(
    () => deriveViewerChanges(comparison, effectiveMode, showMoves),
    [comparison, effectiveMode, showMoves]
  );
  const visibleChanges = React.useMemo(() => {
    const byGroup = filterChanges(allChanges, groups);
    if (!notedOnly) return byGroup;
    return byGroup.filter((c) => {
      const a = annotationFor(c.id);
      return !!a && (!!a.note?.trim() || a.tags.length > 0);
    });
  }, [allChanges, groups, notedOnly, annotationFor]);
  const visibleIds = React.useMemo(() => new Set(visibleChanges.map((c) => c.id)), [visibleChanges]);

  const selectChange = React.useCallback(
    (id: string | null, source: SelectionSource = "list") => {
      // both panes are about to scroll to the change: keep lock-sync out of the way
      scroll.hold(900);
      setSelectionSource(id ? source : null);
      setSelected(id);
      setSelectedMatch(null);
      setSelectionNonce((n) => n + 1);
    },
    [scroll]
  );
  const selectMatch = React.useCallback(
    (id: string | null, source: SelectionSource = "list") => {
      scroll.hold(900);
      setSelectionSource(id ? source : null);
      setSelected(null);
      setSelectedMatch(id);
      setSelectionNonce((n) => n + 1);
    },
    [scroll]
  );
  const setSelectedChangeId = React.useCallback((id: string | null) => selectChange(id, "list"), [selectChange]);

  const stepChange = React.useCallback(
    (dir: 1 | -1 | "first" | "last") => {
      const list = visibleChanges;
      if (!list.length) return;
      if (dir === "first") return selectChange(list[0].id, "keyboard");
      if (dir === "last") return selectChange(list[list.length - 1].id, "keyboard");
      const cur = list.findIndex((c) => c.id === selectedChangeId);
      const next = cur < 0 ? (dir === 1 ? 0 : list.length - 1) : (cur + dir + list.length) % list.length;
      selectChange(list[next].id, "keyboard");
    },
    [visibleChanges, selectedChangeId, selectChange]
  );

  const toggleGroup = React.useCallback((g: FilterGroup) => {
    setGroupsState((cur) => {
      const next = new Set(cur);
      if (next.has(g)) next.delete(g);
      else next.add(g);
      return next;
    });
  }, []);
  const resetGroups = React.useCallback(() => setGroupsState(DEFAULT_GROUPS), []);

  const setZoom = React.useCallback((side: Side, value: number) => {
    const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, value));
    setZoomState((z) => ({ ...z, [side]: clamped }));
  }, []);

  const setAnnotationLocal = React.useCallback((a: Annotation) => {
    setComparison((c) => {
      const rest = (c.annotations ?? []).filter((x) => x.change_id !== a.change_id);
      return { ...c, annotations: [...rest, a] };
    });
  }, []);
  const removeAnnotationLocal = React.useCallback((changeId: string) => {
    setComparison((c) => ({
      ...c,
      annotations: (c.annotations ?? []).filter((x) => x.change_id !== changeId),
    }));
  }, []);

  const value: ViewerState = {
    comparison,
    setComparison,
    allChanges,
    visibleChanges,
    visibleIds,
    groups,
    toggleGroup,
    setGroups: setGroupsState,
    resetGroups,
    notedOnly,
    setNotedOnly,
    selectedChangeId,
    selectionSource,
    selectionNonce,
    selectChange,
    selectedMatchId,
    selectMatch,
    hover,
    setSelectedChangeId,
    stepChange,
    viewMode,
    setViewMode,
    hasPixel,
    effectiveMode,
    layoutMode,
    setLayoutMode,
    singleSide,
    setSingleSide,
    zoom,
    setZoom,
    scrollLock,
    setScrollLock,
    cursorMode,
    setCursorMode,
    showMoves,
    setShowMoves,
    markup,
    setMarkup,
    scroll,
    annotationFor,
    setAnnotationLocal,
    removeAnnotationLocal,
  };

  return <ViewerCtx.Provider value={value}>{children}</ViewerCtx.Provider>;
}

export function useViewer(): ViewerState {
  const ctx = React.useContext(ViewerCtx);
  if (!ctx) throw new Error("useViewer must be used within a ViewerProvider");
  return ctx;
}

/** Markup colours for one change (fill for highlights, marker for the change map). */
export function markupFor(change: Pick<ViewerChange, "kind" | "group"> | undefined, markup: MarkupColors, showMoves: boolean) {
  if (!change) return MARKUP_SWATCH[markup.insertions];
  if (change.kind === "moved" && showMoves) return MARKUP_SWATCH[markup.moves];
  if (change.kind === "removed") return MARKUP_SWATCH[markup.deletions];
  return MARKUP_SWATCH[markup.insertions];
}
