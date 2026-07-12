"use client";
import * as React from "react";
import type {
  Annotation,
  ChangeKind,
  DocumentComparison,
  RenderChangeRef,
} from "@/lib/types";

export type ViewMode = "pixel" | "text";
export type LayoutMode = "side-by-side" | "single";
export type Side = "old" | "new";
export type CursorMode = "scroll" | "select";
export type ViewerFilter = "all" | ChangeKind | "noted";

export const ZOOM_MIN = 0.5;
export const ZOOM_MAX = 3;

/** Text-mode selection id from a diff-block index, and its inverse. */
export const textSelId = (index: number) => `b${index}`;
export function blockIndexFromSelId(id: string | null): number | null {
  if (!id || id[0] !== "b") return null;
  const n = Number(id.slice(1));
  return Number.isInteger(n) ? n : null;
}

/* ------------------------------------------------------------------ *
 * Shared scroll registry: PagePanes register their scroll containers so
 * scroll-lock can proportionally sync the sibling pane, and the HeatStrip
 * can subscribe to the live scroll fraction for its viewport band. A single
 * `syncing` flag guards against the sync feedback loop.
 * ------------------------------------------------------------------ */
type ScrollSubscriber = (fraction: number, side: Side) => void;

export interface ScrollRegistry {
  register: (side: Side, el: HTMLDivElement | null) => void;
  get: (side: Side) => HTMLDivElement | null;
  handleScroll: (side: Side, scrollLock: boolean) => void;
  scrollToFraction: (side: Side, fraction: number) => void;
  subscribe: (cb: ScrollSubscriber) => () => void;
}

function createScrollRegistry(): ScrollRegistry {
  const els: Record<Side, HTMLDivElement | null> = { old: null, new: null };
  const subs = new Set<ScrollSubscriber>();
  let syncing = false;
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
    handleScroll(side, scrollLock) {
      const el = els[side];
      if (!el) return;
      const max = el.scrollHeight - el.clientHeight;
      const frac = max > 0 ? el.scrollTop / max : 0;
      subs.forEach((cb) => cb(frac, side));
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
 * Shared change derivation — one source of truth for the ChangesPanel,
 * HeatStrip and Toolbar prev/next. Mirrors CompareWorkspace's mapping:
 * pixel → render_result.changes, text → diff blocks. `fraction` is the
 * 0..1 vertical position used by the heat strip.
 * ------------------------------------------------------------------ */
export interface ViewerChange {
  id: string; // selection id (pixel: backend id; text: "b{index}")
  kind: ChangeKind;
  removedText?: string;
  addedText?: string;
  moveId?: string;
  blockIndex?: number; // text mode only
  side?: Side; // pixel: side of the primary ref
  page?: number; // pixel: 1-based page of the primary ref
  bbox?: [number, number, number, number];
  fraction: number; // 0..1 vertical position for the heat strip
}

export function deriveViewerChanges(
  comparison: DocumentComparison,
  mode: ViewMode,
  showMoves: boolean
): ViewerChange[] {
  if (mode === "pixel" && comparison.render_result) {
    const rr = comparison.render_result;
    const fracFor = (side: Side, ref: RenderChangeRef): number => {
      const pages = side === "old" ? rr.old.pages : rr.new.pages;
      const total = pages.length || 1;
      const idx = pages.findIndex((p) => p.n === ref.page);
      const i = idx >= 0 ? idx : Math.max(0, ref.page - 1);
      const page = idx >= 0 ? pages[idx] : undefined;
      const cy = page && page.h_pt ? (ref.bbox[1] + ref.bbox[3]) / 2 / page.h_pt : 0;
      return Math.min(1, Math.max(0, (i + cy) / total));
    };
    return rr.changes.map((c) => {
      const side: Side = c.new ? "new" : "old";
      const ref = c.new ?? c.old;
      return {
        id: c.id,
        kind: c.kind,
        removedText: c.old?.text,
        addedText: c.new?.text,
        side,
        page: ref?.page,
        bbox: ref?.bbox,
        fraction: ref ? fracFor(side, ref) : 0,
      };
    });
  }

  const blocks = comparison.diff_result ?? [];
  const total = Math.max(1, blocks.length);
  const out: ViewerChange[] = [];
  blocks.forEach((b, i) => {
    if (b.type === "equal") return;
    const moved = showMoves && !!b.moved;
    let kind: ChangeKind;
    let removedText: string | undefined;
    let addedText: string | undefined;
    if (b.type === "delete") {
      kind = "removed";
      removedText = b.old_text;
    } else if (b.type === "insert") {
      kind = "added";
      addedText = b.new_text;
    } else {
      kind = "modified";
      removedText = b.old_words.filter((w) => w.changed).map((w) => w.text).join(" ");
      addedText = b.new_words.filter((w) => w.changed).map((w) => w.text).join(" ");
    }
    out.push({
      id: textSelId(i),
      kind: moved ? "moved" : kind,
      removedText,
      addedText,
      moveId: b.move_id,
      blockIndex: i,
      fraction: i / total,
    });
  });
  return out;
}

/* ------------------------------------------------------------------ */

interface ViewerState {
  comparison: DocumentComparison;
  setComparison: React.Dispatch<React.SetStateAction<DocumentComparison>>;

  selectedChangeId: string | null;
  setSelectedChangeId: (id: string | null) => void;

  viewMode: ViewMode; // requested
  setViewMode: (m: ViewMode) => void;
  hasPixel: boolean; // render availability
  effectiveMode: ViewMode; // derived from availability

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

  filter: ViewerFilter;
  setFilter: (f: ViewerFilter) => void;

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
  const [selectedChangeId, setSelectedChangeId] = React.useState<string | null>(null);
  const [viewMode, setViewMode] = React.useState<ViewMode>("pixel");
  const [layoutMode, setLayoutMode] = React.useState<LayoutMode>("side-by-side");
  const [singleSide, setSingleSide] = React.useState<Side>("new");
  const [zoom, setZoomState] = React.useState<Record<Side, number>>({ old: 1, new: 1 });
  const [scrollLock, setScrollLock] = React.useState(true);
  const [cursorMode, setCursorMode] = React.useState<CursorMode>("scroll");
  const [showMoves, setShowMoves] = React.useState(true);
  const [filter, setFilter] = React.useState<ViewerFilter>("all");

  const scrollRef = React.useRef<ScrollRegistry | null>(null);
  if (scrollRef.current === null) scrollRef.current = createScrollRegistry();
  const scroll = scrollRef.current;

  const hasPixel = comparison.render_status === "completed" && !!comparison.render_result;
  const effectiveMode: ViewMode = hasPixel && viewMode === "pixel" ? "pixel" : "text";

  const setZoom = React.useCallback((side: Side, value: number) => {
    const clamped = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, value));
    setZoomState((z) => ({ ...z, [side]: clamped }));
  }, []);

  const annotationFor = React.useCallback(
    (changeId: string) => comparison.annotations?.find((a) => a.change_id === changeId),
    [comparison.annotations]
  );
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
    selectedChangeId,
    setSelectedChangeId,
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
    filter,
    setFilter,
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
