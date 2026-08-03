"use client";
import * as React from "react";
import { submissionPageImageUrl } from "@/lib/api";
import { normalizeSeverity } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Violation } from "@/lib/types";

/**
 * Single-side pixel document view for a PDF submission — a TRIMMED copy of
 * compare-viewer/PagePane.tsx's box-positioning math only (no zoom, no
 * dual-side scroll-lock/state machine, no search). One <img> per rendered
 * page from GET /submissions/{id}/pages/{n}; violation boxes are positioned
 * from anchor_page/anchor_bbox exactly like PagePane positions change boxes
 * from render_result — clicking a box calls the same onSelect(violationId)
 * DocumentPane already uses, so ReviewTab can swap panes without either side
 * knowing about the other.
 */

interface Props {
  submissionId: string;
  violations: Violation[];
  selectedViolationId: string | null;
  onSelect: (id: string) => void;
}

// pdf_render_service.render_pages rasterizes at a fixed scale=2.0 (~144 DPI)
// from PDF points (see backend/app/services/pdf_render_service.py). anchor_bbox
// is stored in those same points (matching PositionedWord), so the pixel
// percentage for a box is (bbox_point * RENDER_SCALE) / naturalPixelSize —
// there is no separate page-dimensions endpoint to read w_pt/h_pt from, so
// the rendered <img>'s own naturalWidth/naturalHeight stands in for it.
const RENDER_SCALE = 2.0;

const boxDomId = (violationId: string) => `pdfpane-box-${violationId}`;

interface PageBox {
  violation: Violation;
  x0: number;
  y0: number;
  x1: number;
  y1: number;
}

export function PdfPagePane({ submissionId, violations, selectedViolationId, onSelect }: Props) {
  // There is no page-count endpoint for a submission (unlike Compare's
  // render_result, which embeds the full page list) — discover pages
  // sequentially: request page 1, and keep requesting the next one each time
  // the current last page loads; stop the first time a page 404s.
  const [pages, setPages] = React.useState<number[]>([1]);
  const stoppedRef = React.useRef(false);

  const handlePageLoad = React.useCallback((n: number) => {
    if (stoppedRef.current) return;
    setPages((prev) => (prev[prev.length - 1] === n ? [...prev, n + 1] : prev));
  }, []);
  const handlePageError = React.useCallback((n: number) => {
    stoppedRef.current = true;
    setPages((prev) => prev.filter((p) => p !== n));
  }, []);

  const boxesByPage = React.useMemo(() => {
    const m = new Map<number, PageBox[]>();
    for (const v of violations) {
      if (v.anchor_page == null || !v.anchor_bbox) continue;
      const [x0, y0, x1, y1] = v.anchor_bbox;
      const list = m.get(v.anchor_page) ?? [];
      list.push({ violation: v, x0, y0, x1, y1 });
      m.set(v.anchor_page, list);
    }
    return m;
  }, [violations]);

  const containerRef = React.useRef<HTMLDivElement>(null);

  // Scroll the selected violation's box into view, mirroring DocumentPane's
  // selected-<mark> scroll effect.
  React.useEffect(() => {
    if (!selectedViolationId || !containerRef.current) return;
    const el = containerRef.current.querySelector<HTMLElement>(
      `#${CSS.escape(boxDomId(selectedViolationId))}`
    );
    if (el) el.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [selectedViolationId]);

  return (
    // The document is the subject of this screen: the pane spends its width on
    // the page, not on padding, and no max-width caps it short of the column.
    <div ref={containerRef} className="min-h-0 flex-1 overflow-y-auto bg-background p-2">
      <div className="space-y-3">
        {pages.map((n) => (
          <PdfPageTile
            key={n}
            submissionId={submissionId}
            n={n}
            boxes={boxesByPage.get(n) ?? []}
            selectedViolationId={selectedViolationId}
            onSelect={onSelect}
            onLoad={() => handlePageLoad(n)}
            onError={() => handlePageError(n)}
          />
        ))}
        {pages.length === 0 && (
          <p className="p-8 text-center text-sm text-muted-foreground">
            No rendered pages found for this document.
          </p>
        )}
      </div>
    </div>
  );
}

function PdfPageTile({
  submissionId,
  n,
  boxes,
  selectedViolationId,
  onSelect,
  onLoad,
  onError,
}: {
  submissionId: string;
  n: number;
  boxes: PageBox[];
  selectedViolationId: string | null;
  onSelect: (id: string) => void;
  onLoad: () => void;
  onError: () => void;
}) {
  // Natural pixel size of the loaded page image — stands in for w_pt/h_pt
  // (see RENDER_SCALE comment above) so box percentages can be computed.
  const [natural, setNatural] = React.useState<{ w: number; h: number } | null>(null);
  const pct = (v: number, dim: number) => `${(v / dim) * 100}%`;

  return (
    // The tile fills the pane (w-full) and stops at the image's own pixel size
    // so a wide monitor gets a big page, never a blurry upscaled one. Boxes are
    // unaffected: the <img> is still `block w-full` of this element with no
    // padding between them, so this box and the rendered image are the same
    // rectangle, and the percentages below stay percentages of the image.
    <div
      className="relative mx-auto w-full border border-border bg-background shadow-sm"
      style={{ maxWidth: natural?.w }}
    >
      <img
        src={submissionPageImageUrl(submissionId, n)}
        alt={`Page ${n}`}
        loading="lazy"
        draggable={false}
        className="block w-full select-none"
        onLoad={(e) => {
          const img = e.currentTarget;
          setNatural({ w: img.naturalWidth, h: img.naturalHeight });
          onLoad();
        }}
        onError={onError}
      />
      {natural &&
        boxes.map((b) => {
          const selected = selectedViolationId === b.violation.id;
          const sev = normalizeSeverity(b.violation.severity);
          const color =
            sev === "critical"
              ? "bg-sev-critical/25 ring-sev-critical/60"
              : sev === "high"
              ? "bg-sev-high/25 ring-sev-high/60"
              : sev === "medium"
              ? "bg-sev-medium/25 ring-sev-medium/60"
              : "bg-sev-low/25 ring-sev-low/60";
          return (
            <button
              key={b.violation.id}
              id={boxDomId(b.violation.id)}
              type="button"
              onClick={() => onSelect(b.violation.id)}
              title={b.violation.description}
              className={cn(
                "absolute rounded-[1px] ring-1 transition-shadow",
                color,
                selected && "ring-2 ring-primary"
              )}
              style={{
                left: pct(b.x0 * RENDER_SCALE, natural.w),
                top: pct(b.y0 * RENDER_SCALE, natural.h),
                width: pct((b.x1 - b.x0) * RENDER_SCALE, natural.w),
                height: pct((b.y1 - b.y0) * RENDER_SCALE, natural.h),
              }}
            />
          );
        })}
    </div>
  );
}
