"use client";
import * as React from "react";
import { toast } from "sonner";
import {
  ChevronDown,
  ChevronUp,
  FileText,
  Minus,
  Plus,
  RotateCcw,
  Search,
  X,
} from "lucide-react";
import type { RenderPage, SearchHit } from "@/lib/types";
import { comparisonPageImageUrl, searchComparison } from "@/lib/api";
import { sideLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import { matchesBySidePage, outlinePolygons, type Rect, type SideMatch } from "@/lib/compareModel";
import {
  useViewer,
  useHoverTarget,
  markupFor,
  type HoverStore,
  type MarkupColors,
  type Side,
  type ViewerChange,
  type SelectionSource,
} from "./ViewerContext";

const SELECT_RING = "#00B2FF";
/** Draftable outline colours: hover (and the linked counterpart) / selected. */
export const OUTLINE_HOVER = "#5195F6";
export const OUTLINE_SELECTED = "#00B2FF";
/** a pointer that moved further than this between down and up was a pan, not a click */
const DRAG_PX = 4;

const boxDomId = (side: Side, changeId: string) => `vwr-${side}-${changeId}`;

const BLINK_MS = 1400;

/** Replay a short attention blink on every box/caret of the selected change (both the
 *  clicked side and its counterpart). Returns a cleanup that stops it early. */
function blink(cont: HTMLElement, els: HTMLElement[], seq: number) {
  cont.querySelectorAll("[data-blink]").forEach((n) => n.removeAttribute("data-blink"));
  for (const el of els) {
    el.removeAttribute("data-blink");
    void el.offsetWidth; // restart the CSS animation on repeat clicks
    el.setAttribute("data-blink", "true");
    el.setAttribute("data-blink-seq", String(seq));
  }
  const t = setTimeout(() => els.forEach((el) => el.removeAttribute("data-blink")), BLINK_MS);
  return () => clearTimeout(t);
}

/** Centre `el` inside its scroll container `cont`. */
export function revealIn(cont: HTMLElement, el: HTMLElement) {
  const cr = cont.getBoundingClientRect();
  const er = el.getBoundingClientRect();
  const top = cont.scrollTop + (er.top - cr.top) - (cr.height - er.height) / 2;
  const left = cont.scrollLeft + (er.left - cr.left) - (cr.width - er.width) / 2;
  // Instant, not smooth: two concurrent programmatic smooth scrolls (one per pane) are
  // serialised by Chromium, so the second pane arrived seconds late and scroll-lock then
  // dragged the first one away. Jumping lands both panes on their targets in one frame.
  if (typeof cont.scrollTo === "function") cont.scrollTo({ top: Math.max(0, top), left: Math.max(0, left), behavior: "auto" });
  else cont.scrollTop = Math.max(0, top);
}
const hitDomId = (side: Side, i: number) => `vwr-${side}-hit-${i}`;

export function PagePane({ side, className }: { side: Side; className?: string }) {
  const {
    comparison,
    zoom,
    setZoom,
    cursorMode,
    scrollLock,
    scroll,
    selectedChangeId,
    selectionNonce,
    selectChange,
    selectedMatchId,
    selectMatch,
    hover,
    showMoves,
    allChanges,
    visibleIds,
    markup,
  } = useViewer();

  const render = comparison.render_result!;
  const pages = side === "old" ? render.old.pages : render.new.pages;
  const contentType = side === "old" ? comparison.old_content_type : comparison.new_content_type;
  const isPdf = contentType === "pdf";
  const paneName = sideLabel(comparison, side);

  const changeById = React.useMemo(() => {
    const m = new Map<string, ViewerChange>();
    for (const c of allChanges) m.set(c.id, c);
    return m;
  }, [allChanges]);

  // one-sided changes: caret at the insertion/deletion point on this side
  const anchorsByPage = React.useMemo(() => {
    const m = new Map<number, ViewerChange[]>();
    for (const c of allChanges) {
      const ref = side === "old" ? c.old : c.new;
      if (!ref?.anchor || !visibleIds.has(c.id)) continue;
      const list = m.get(ref.page) ?? [];
      list.push(c);
      m.set(ref.page, list);
    }
    return m;
  }, [allChanges, visibleIds, side]);

  // unchanged text linked across the documents (Draftable hover/click units)
  const matchesByPage = React.useMemo(() => matchesBySidePage(render.matches)[side], [render.matches, side]);

  const scrollElRef = React.useRef<HTMLDivElement>(null);
  const pageEls = React.useRef<Map<number, HTMLDivElement>>(new Map());

  const [currentPage, setCurrentPage] = React.useState(pages[0]?.n ?? 1);
  const [query, setQuery] = React.useState("");
  const [hits, setHits] = React.useState<SearchHit[]>([]);
  const [hitIndex, setHitIndex] = React.useState(0);

  const z = zoom[side];

  // Register the scroll container so scroll-lock and the heat strip can reach it.
  React.useEffect(() => {
    scroll.register(side, scrollElRef.current);
    return () => scroll.register(side, null);
  }, [scroll, side]);

  const updateCurrentPage = React.useCallback(() => {
    const cont = scrollElRef.current;
    if (!cont) return;
    const top = cont.getBoundingClientRect().top;
    let cur = pages[0]?.n ?? 1;
    for (const p of pages) {
      const el = pageEls.current.get(p.n);
      if (!el) continue;
      if (el.getBoundingClientRect().top - top <= 8) cur = p.n;
      else break;
    }
    setCurrentPage(cur);
  }, [pages]);

  // Two-way navigation: whenever a change is selected (from either pane, the list or
  // the change map) this pane scrolls to its own text - or, for a one-sided change,
  // to the anchor caret marking where the text was inserted/deleted.
  // Each pane scrolls only its own container (two concurrent smooth scrollIntoView
  // calls interrupt each other in Chromium, leaving one pane short of its target).
  React.useEffect(() => {
    const cont = scrollElRef.current;
    if (!selectedChangeId || !cont) return;
    cont.querySelectorAll("[data-revealed]").forEach((n) => n.removeAttribute("data-revealed"));
    const matches = Array.from(
      cont.querySelectorAll<HTMLElement>(`[data-change-id="${CSS.escape(selectedChangeId)}"]`)
    );
    if (!matches.length) return;
    matches[0].setAttribute("data-revealed", "true");
    revealIn(cont, matches[0]);
    return blink(cont, matches, selectionNonce);
  }, [selectedChangeId, selectionNonce, side]);

  // Clicked unchanged text: both panes bring their copy of the sentence to the same place.
  React.useEffect(() => {
    const cont = scrollElRef.current;
    if (!selectedMatchId || !cont) return;
    const el = cont.querySelector<SVGElement>(`[data-match-id="${CSS.escape(selectedMatchId)}"]`);
    if (el) revealIn(cont, el as unknown as HTMLElement);
  }, [selectedMatchId, selectionNonce, side]);

  // Keep the active search hit visible.
  React.useEffect(() => {
    if (!hits.length || !scrollElRef.current) return;
    const el = scrollElRef.current.querySelector<HTMLElement>(`#${CSS.escape(hitDomId(side, hitIndex))}`);
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [hitIndex, hits, side]);

  const runSearch = async () => {
    const q = query.trim();
    if (!q) {
      setHits([]);
      return;
    }
    try {
      const res = await searchComparison(comparison.id, side, q);
      setHits(res.hits);
      setHitIndex(0);
      if (res.hits.length === 0) toast(`No matches for “${q}”`);
    } catch (e) {
      toast.error(`Search failed: ${(e as Error).message}`);
    }
  };

  const clearSearch = () => {
    setQuery("");
    setHits([]);
    setHitIndex(0);
  };

  const stepHit = (dir: 1 | -1) => {
    if (!hits.length) return;
    setHitIndex((i) => (i + dir + hits.length) % hits.length);
  };

  const jumpToPage = (n: number) => {
    const clamped = Math.min(pages[pages.length - 1]?.n ?? n, Math.max(pages[0]?.n ?? n, n));
    pageEls.current.get(clamped)?.scrollIntoView({ block: "start" });
  };

  // Drag-to-pan (Scroll cursor mode).
  const drag = React.useRef({ active: false, moved: false, x: 0, y: 0, left: 0, top: 0 });
  const wasDrag = React.useCallback(() => drag.current.moved, []);
  const onPointerDown = (e: React.PointerEvent) => {
    drag.current.moved = false;
    if (cursorMode !== "scroll") return;
    const cont = scrollElRef.current;
    if (!cont) return;
    // No pointer capture yet: capturing on pointer-down retargets the click to the scroll
    // container, so a plain click would never reach a highlight or a sentence.
    drag.current = { active: true, moved: false, x: e.clientX, y: e.clientY, left: cont.scrollLeft, top: cont.scrollTop };
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current.active) return;
    const cont = scrollElRef.current;
    if (!cont) return;
    if (!drag.current.moved) {
      if (Math.abs(e.clientX - drag.current.x) + Math.abs(e.clientY - drag.current.y) <= DRAG_PX) return;
      drag.current.moved = true; // a real pan: now keep the pointer while dragging
      cont.setPointerCapture?.(e.pointerId);
    }
    cont.scrollLeft = drag.current.left - (e.clientX - drag.current.x);
    cont.scrollTop = drag.current.top - (e.clientY - drag.current.y);
  };
  const onPointerUp = (e: React.PointerEvent) => {
    if (!drag.current.active) return;
    drag.current.active = false;
    if (scrollElRef.current?.hasPointerCapture?.(e.pointerId)) scrollElRef.current.releasePointerCapture(e.pointerId);
  };

  return (
    <div className={cn("flex min-h-0 min-w-0 flex-col bg-surface", className)}>
      {/* Header */}
      <div className="flex flex-wrap items-center gap-2 border-b border-border bg-background px-3 py-1.5">
        <span className="inline-flex min-w-0 items-center gap-1.5 text-[12px] font-medium">
          <FileText className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
          <span className="max-w-[240px] truncate" title={paneName}>{paneName}</span>
          <span className="micro-label shrink-0">{(contentType || "").toUpperCase()}</span>
        </span>

        {/* Per-pane search */}
        <div className="flex items-center gap-1" title={isPdf ? undefined : "Search needs a PDF on this side"}>
          <div className="relative">
            <Search className="pointer-events-none absolute left-1.5 top-1/2 h-3 w-3 -translate-y-1/2 text-muted-foreground" />
            <input
              value={query}
              disabled={!isPdf}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") runSearch();
                else if (e.key === "Escape") clearSearch();
              }}
              placeholder="Search…"
              className="h-6 w-32 rounded-sm border border-border bg-background pl-6 pr-1 text-[11px] placeholder:text-muted-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary disabled:opacity-40"
            />
          </div>
          {hits.length > 0 && (
            <>
              <span className="font-mono text-[10px] text-muted-foreground">
                {hitIndex + 1}/{hits.length}
              </span>
              <button type="button" onClick={() => stepHit(-1)} className="text-muted-foreground hover:text-foreground" title="Previous match">
                <ChevronUp className="h-3.5 w-3.5" />
              </button>
              <button type="button" onClick={() => stepHit(1)} className="text-muted-foreground hover:text-foreground" title="Next match">
                <ChevronDown className="h-3.5 w-3.5" />
              </button>
              <button type="button" onClick={clearSearch} className="text-muted-foreground hover:text-foreground" title="Clear search">
                <X className="h-3.5 w-3.5" />
              </button>
            </>
          )}
        </div>

        <div className="ml-auto flex items-center gap-2">
          {/* Page indicator + jump */}
          <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <input
              key={currentPage}
              defaultValue={currentPage}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  const n = Number((e.target as HTMLInputElement).value);
                  if (Number.isFinite(n)) jumpToPage(n);
                }
              }}
              className="h-6 w-10 rounded-sm border border-border bg-background px-1 text-center text-[11px] focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-primary"
            />
            of {pages.length}
          </span>

          {/* Zoom */}
          <div className="flex items-center gap-0.5">
            <button type="button" onClick={() => setZoom(side, z - 0.25)} className="rounded-sm border border-border p-0.5 text-muted-foreground hover:text-foreground" title="Zoom out">
              <Minus className="h-3 w-3" />
            </button>
            <button type="button" onClick={() => setZoom(side, 1)} className="min-w-[38px] rounded-sm border border-border px-1 py-0.5 font-mono text-[10px] text-muted-foreground hover:text-foreground" title="Reset zoom">
              {Math.round(z * 100)}%
            </button>
            <button type="button" onClick={() => setZoom(side, z + 0.25)} className="rounded-sm border border-border p-0.5 text-muted-foreground hover:text-foreground" title="Zoom in">
              <Plus className="h-3 w-3" />
            </button>
            <button type="button" onClick={() => setZoom(side, 1)} className="rounded-sm border border-border p-0.5 text-muted-foreground hover:text-foreground" title="Fit width">
              <RotateCcw className="h-3 w-3" />
            </button>
          </div>
        </div>
      </div>

      {/* Body */}
      <div
        ref={scrollElRef}
        onScroll={() => {
          scroll.handleScroll(side, scrollLock);
          updateCurrentPage();
        }}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        className={cn(
          "min-h-0 flex-1 overflow-auto p-3",
          cursorMode === "scroll" ? "cursor-grab active:cursor-grabbing" : "cursor-text"
        )}
      >
        <div className="mx-auto space-y-3" style={{ width: `${z * 100}%` }}>
          {pages.map((p) => (
            <PageTile
              key={p.n}
              side={side}
              comparisonId={comparison.id}
              page={p}
              changeById={changeById}
              visibleIds={visibleIds}
              anchors={anchorsByPage.get(p.n) ?? []}
              markup={markup}
              showMoves={showMoves}
              selectedChangeId={selectedChangeId}
              onSelect={(id) => {
                if (!wasDrag()) selectChange(id, side as SelectionSource);
              }}
              matches={matchesByPage.get(p.n) ?? []}
              selectedMatchId={selectedMatchId}
              onSelectMatch={(id) => {
                if (!wasDrag()) selectMatch(id, side as SelectionSource);
              }}
              hover={hover}
              hits={hits}
              hitIndex={hitIndex}
              registerEl={(el) => {
                if (el) pageEls.current.set(p.n, el);
                else pageEls.current.delete(p.n);
              }}
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function PageTile({
  side,
  comparisonId,
  page,
  changeById,
  visibleIds,
  anchors,
  markup,
  showMoves,
  selectedChangeId,
  onSelect,
  matches,
  selectedMatchId,
  onSelectMatch,
  hover,
  hits,
  hitIndex,
  registerEl,
}: {
  side: Side;
  comparisonId: string;
  page: RenderPage;
  changeById: Map<string, ViewerChange>;
  visibleIds: ReadonlySet<string>;
  anchors: ViewerChange[];
  markup: MarkupColors;
  showMoves: boolean;
  selectedChangeId: string | null;
  onSelect: (id: string) => void;
  matches: SideMatch[];
  selectedMatchId: string | null;
  onSelectMatch: (id: string) => void;
  hover: HoverStore;
  hits: SearchHit[];
  hitIndex: number;
  registerEl: (el: HTMLDivElement | null) => void;
}) {
  const pct = (a: number, b: number) => `${(a / b) * 100}%`;
  return (
    <div
      ref={registerEl}
      data-page={page.n}
      className="relative w-full"
      style={{ aspectRatio: `${page.w_pt} / ${page.h_pt}` }}
    >
      <img
        src={comparisonPageImageUrl(comparisonId, side, page.n)}
        alt={`${side} page ${page.n}`}
        loading="lazy"
        draggable={false}
        className="block w-full select-none border border-border"
      />
      <MatchHitLayer page={page} matches={matches} hover={hover} onSelect={onSelectMatch} />
      {page.boxes.map((b, i) => {
        if (!visibleIds.has(b.change_id)) return null;
        const ch = changeById.get(b.change_id);
        const label = changeLabel(ch, side);
        // the original shows what was removed/replaced, the revised what was added/replaced
        // Draftable palette for every record: the original shows removed/replaced text in
        // red, the revised shows added/replaced text in blue, moves in green
        const sw =
          ch && ch.kind !== "moved"
            ? markupFor({ kind: side === "old" ? "removed" : "added", group: ch.group }, markup, showMoves)
            : markupFor(ch, markup, showMoves);
        const selected = selectedChangeId === b.change_id;
        const boxId = b.box_id || `${b.change_id}-p${page.n}-b${i}`;
        return (
          <button
            key={boxId}
            id={boxDomId(side, boxId)}
            data-change-id={b.change_id}
            data-group={ch?.group}
            data-selected={selected || undefined}
            type="button"
            title={label}
            aria-label={label}
            onClick={() => onSelect(b.change_id)}
            onMouseEnter={() => hover.set({ kind: "change", id: b.change_id })}
            onMouseLeave={() => hover.clear(b.change_id)}
            className="absolute rounded-[1px] mix-blend-multiply transition-shadow"
            style={{
              left: pct(b.x0, page.w_pt),
              top: pct(b.y0, page.h_pt),
              width: pct(b.x1 - b.x0, page.w_pt),
              height: pct(b.y1 - b.y0, page.h_pt),
              background: sw.fill,
              outline: selected ? `2px solid ${SELECT_RING}` : "none",
              outlineOffset: selected ? 1 : 0,
            }}
          />
        );
      })}
      {anchors.map((c) => {
        const ref = side === "old" ? c.old! : c.new!;
        if (ref.page !== page.n) return null;
        const selected = selectedChangeId === c.id;
        // a caret on the original marks where text was inserted; on the revised, where it was deleted
        const sw = markupFor({ kind: side === "old" ? "added" : "removed", group: c.group }, markup, showMoves);
        const label = side === "old" ? "Text inserted here in the revised document" : "Text deleted here from the original";
        return (
          <button
            key={`anchor-${c.id}`}
            data-change-id={c.id}
            data-anchor="true"
            data-selected={selected || undefined}
            type="button"
            title={label}
            aria-label={label}
            onClick={() => onSelect(c.id)}
            onMouseEnter={() => hover.set({ kind: "change", id: c.id })}
            onMouseLeave={() => hover.clear(c.id)}
            className="absolute"
            style={{
              left: `calc(${pct(ref.bbox[0], page.w_pt)} - 1px)`,
              top: pct(ref.bbox[1], page.h_pt),
              width: 3,
              height: pct(Math.max(6, ref.bbox[3] - ref.bbox[1]), page.h_pt),
              background: sw.text,
              outline: selected ? `2px solid ${SELECT_RING}` : "none",
              outlineOffset: 1,
            }}
          >
            <span
              className="absolute left-1/2 top-full -translate-x-1/2"
              style={{
                width: 0,
                height: 0,
                borderLeft: "4px solid transparent",
                borderRight: "4px solid transparent",
                borderBottom: `5px solid ${sw.text}`,
              }}
            />
          </button>
        );
      })}
      <OutlineLayer page={page} side={side} matches={matches} anchors={anchors} visibleIds={visibleIds}
        hover={hover} selectedMatchId={selectedMatchId} />
      {hits.map((h, gi) =>
        h.page !== page.n ? null : (
          <div
            key={`hit-${gi}`}
            id={hitDomId(side, gi)}
            className={cn(
              "pointer-events-none absolute rounded-[1px] ring-1",
              gi === hitIndex ? "bg-primary/20 ring-2 ring-primary" : "ring-primary/40"
            )}
            style={{
              left: pct(h.bbox[0], page.w_pt),
              top: pct(h.bbox[1], page.h_pt),
              width: pct(h.bbox[2] - h.bbox[0], page.w_pt),
              height: pct(h.bbox[3] - h.bbox[1], page.h_pt),
            }}
          />
        )
      )}
    </div>
  );
}

function changeLabel(ch: ViewerChange | undefined, side: Side): string {
  if (!ch) return side === "old" ? "Deleted text" : "Added text";
  if (ch.category === "typographic") return `Typographic change (${ch.subtype ?? "formatting"})`;
  if (ch.category === "placeholder") return "Template placeholder";
  if (ch.category === "renumbering") return "Renumbering";
  if (ch.group === "headers_footers") return "Header / footer change";
  if (ch.category === "layout") return "Repeated table header";
  if (ch.kind === "moved") return "Moved text";
  if (ch.kind === "added") return "Inserted text";
  if (ch.kind === "removed") return "Deleted text";
  if (ch.changeType === "numeric_only") return "Numeric change";
  if (ch.changeType === "identifier_only") return "Identifier change";
  return "Text replacement";
}

const pts = (p: [number, number][]) => p.map(([x, y]) => `${x},${y}`).join(" ");

/** Invisible line-hugging hit areas over unchanged text: hover outlines the sentence and
 *  its counterpart on the other document; click selects it and aligns both panes. */
function MatchHitLayer({
  page,
  matches,
  hover,
  onSelect,
}: {
  page: RenderPage;
  matches: SideMatch[];
  hover: HoverStore;
  onSelect: (id: string) => void;
}) {
  const shapes = React.useMemo(
    () => matches.map((m) => ({ id: m.id, polys: outlinePolygons(m.rects) })),
    [matches]
  );
  if (!shapes.length) return null;
  return (
    <svg
      className="absolute inset-0 h-full w-full"
      viewBox={`0 0 ${page.w_pt} ${page.h_pt}`}
      preserveAspectRatio="none"
      style={{ pointerEvents: "none" }}
      aria-hidden="true"
    >
      {shapes.map((m) =>
        m.polys.map((poly, k) => (
          <polygon
            key={`${m.id}-${k}`}
            data-match-id={m.id}
            points={pts(poly)}
            fill="transparent"
            style={{ pointerEvents: "all", cursor: "pointer" }}
            onMouseEnter={() => hover.set({ kind: "match", id: m.id })}
            onMouseLeave={() => hover.clear(m.id)}
            onClick={() => onSelect(m.id)}
          />
        ))
      )}
    </svg>
  );
}

/** Draftable outline: a stroke drawn only outside the text (masked inside); hover blue for
 *  the hovered unit/change and its counterpart, cyan for the selected unit. */
function OutlineLayer({
  page,
  side,
  matches,
  anchors,
  visibleIds,
  hover,
  selectedMatchId,
}: {
  page: RenderPage;
  side: Side;
  matches: SideMatch[];
  anchors: ViewerChange[];
  visibleIds: ReadonlySet<string>;
  hover: HoverStore;
  selectedMatchId: string | null;
}) {
  const target = useHoverTarget(hover);
  const uid = React.useId().replace(/:/g, "");

  const rectsFor = (kind: "match" | "change", id: string): Rect[] => {
    if (kind === "match") return matches.find((m) => m.id === id)?.rects ?? [];
    if (!visibleIds.has(id)) return [];
    const boxes: Rect[] = page.boxes.filter((b) => b.change_id === id).map((b) => [b.x0, b.y0, b.x1, b.y1]);
    const a = anchors.find((c) => c.id === id);
    const ref = a ? (side === "old" ? a.old : a.new) : undefined;
    if (ref && ref.page === page.n) boxes.push([ref.bbox[0] - 1, ref.bbox[1], ref.bbox[0] + 2, ref.bbox[3]]);
    return boxes;
  };

  const layers: { key: "sel" | "hov"; kind: string; id: string; color: string; width: number; polys: [number, number][][] }[] = [];
  if (selectedMatchId) {
    const r = rectsFor("match", selectedMatchId);
    if (r.length) layers.push({ key: "sel", kind: "match", id: selectedMatchId, color: OUTLINE_SELECTED, width: 4, polys: outlinePolygons(r) });
  }
  if (target && !(target.kind === "match" && target.id === selectedMatchId)) {
    const r = rectsFor(target.kind, target.id);
    if (r.length) layers.push({ key: "hov", kind: target.kind, id: target.id, color: OUTLINE_HOVER, width: 3, polys: outlinePolygons(r) });
  }
  if (!layers.length) return null;
  return (
    <svg
      className="pointer-events-none absolute inset-0 h-full w-full"
      viewBox={`0 0 ${page.w_pt} ${page.h_pt}`}
      preserveAspectRatio="none"
      aria-hidden="true"
    >
      {layers.map((l) =>
        l.polys.map((poly, k) => {
          const mask = `outline-${uid}-${l.key}-${k}`;
          return (
            <g key={`${l.key}-${k}`} data-outline={l.key === "sel" ? "selected" : "hover"} data-target-kind={l.kind} data-target-id={l.id}>
              <mask id={mask} maskUnits="userSpaceOnUse">
                <rect x={0} y={0} width="100%" height="100%" fill="#FFF" />
                <polygon points={pts(poly)} fill="#000" stroke="#FFF" strokeWidth={0.3} />
              </mask>
              <polygon points={pts(poly)} fill="transparent" stroke={l.color} strokeWidth={l.width} mask={`url(#${mask})`} />
            </g>
          );
        })
      )}
    </svg>
  );
}
