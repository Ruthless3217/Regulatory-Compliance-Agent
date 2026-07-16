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
import type { ChangeKind, RenderPage, SearchHit } from "@/lib/types";
import { comparisonPageImageUrl, searchComparison } from "@/lib/api";
import { sideLabel } from "@/lib/format";
import { cn } from "@/lib/utils";
import { useViewer, type Side } from "./ViewerContext";

const boxDomId = (side: Side, changeId: string) => `vwr-${side}-${changeId}`;
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
    setSelectedChangeId,
    showMoves,
  } = useViewer();

  const render = comparison.render_result!;
  const pages = side === "old" ? render.old.pages : render.new.pages;
  const contentType = side === "old" ? comparison.old_content_type : comparison.new_content_type;
  const isPdf = contentType === "pdf";
  const paneName = sideLabel(comparison, side);

  const kindById = React.useMemo(() => {
    const m = new Map<string, ChangeKind>();
    for (const c of render.changes) m.set(c.id, c.kind);
    return m;
  }, [render.changes]);

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

  // Scroll the selected change's box into view (both panes react independently).
  React.useEffect(() => {
    if (!selectedChangeId || !scrollElRef.current) return;
    const el = scrollElRef.current.querySelector<HTMLElement>(
      `#${CSS.escape(boxDomId(side, selectedChangeId))}`
    );
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [selectedChangeId, side]);

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
  const drag = React.useRef({ active: false, x: 0, y: 0, left: 0, top: 0 });
  const onPointerDown = (e: React.PointerEvent) => {
    if (cursorMode !== "scroll") return;
    const cont = scrollElRef.current;
    if (!cont) return;
    drag.current = { active: true, x: e.clientX, y: e.clientY, left: cont.scrollLeft, top: cont.scrollTop };
    cont.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: React.PointerEvent) => {
    if (!drag.current.active) return;
    const cont = scrollElRef.current;
    if (!cont) return;
    cont.scrollLeft = drag.current.left - (e.clientX - drag.current.x);
    cont.scrollTop = drag.current.top - (e.clientY - drag.current.y);
  };
  const onPointerUp = (e: React.PointerEvent) => {
    if (!drag.current.active) return;
    drag.current.active = false;
    scrollElRef.current?.releasePointerCapture?.(e.pointerId);
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
              kindById={kindById}
              showMoves={showMoves}
              selectedChangeId={selectedChangeId}
              onSelect={setSelectedChangeId}
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
  kindById,
  showMoves,
  selectedChangeId,
  onSelect,
  hits,
  hitIndex,
  registerEl,
}: {
  side: Side;
  comparisonId: string;
  page: RenderPage;
  kindById: Map<string, ChangeKind>;
  showMoves: boolean;
  selectedChangeId: string | null;
  onSelect: (id: string) => void;
  hits: SearchHit[];
  hitIndex: number;
  registerEl: (el: HTMLDivElement | null) => void;
}) {
  const pct = (a: number, b: number) => `${(a / b) * 100}%`;
  return (
    <div
      ref={registerEl}
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
      {page.boxes.map((b, i) => {
        const moved = kindById.get(b.change_id) === "moved";
        const color = moved
          ? "bg-violet-600/25 ring-violet-600/50"
          : b.type === "removed"
          ? "bg-sev-critical/25 ring-sev-critical/50"
          : "bg-success/25 ring-success/50";
        const selected = selectedChangeId === b.change_id;
        return (
          <button
            key={i}
            id={boxDomId(side, b.change_id)}
            type="button"
            onClick={() => onSelect(b.change_id)}
            className={cn(
              "absolute rounded-[1px] ring-1 transition-shadow",
              color,
              moved && !showMoves && "opacity-10",
              selected && "ring-2 ring-primary"
            )}
            style={{
              left: pct(b.x0, page.w_pt),
              top: pct(b.y0, page.h_pt),
              width: pct(b.x1 - b.x0, page.w_pt),
              height: pct(b.y1 - b.y0, page.h_pt),
            }}
          />
        );
      })}
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
