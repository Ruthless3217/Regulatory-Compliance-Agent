"use client";
import * as React from "react";
import { useViewer, markupFor, type Side, type ViewerChange } from "./ViewerContext";

export const MAP_SELECTED = "#00B2FF";
export const MAP_LINKED = "#5195F6";
const MIN_HEIGHT = 0.003; // 0.3 % of the document, like Draftable's thinnest marker

/**
 * Draftable-style centre scrollbar between the two documents: the left lane maps the
 * original, the right lane the revised; each visible change draws a marker on the lane
 * of every side where it has text (or an anchor), at its position in that document.
 * The grey thumb shows the visible part of the documents and can be dragged.
 */
export function CenterChangeMap() {
  const {
    visibleChanges,
    selectedChangeId,
    selectionSource,
    selectChange,
    scroll,
    markup,
    showMoves,
    effectiveMode,
    layoutMode,
    singleSide,
  } = useViewer();

  const [view, setView] = React.useState<{ fraction: number; side: Side }>({ fraction: 0, side: "new" });
  React.useEffect(() => scroll.subscribe((fraction, side) => setView({ fraction, side })), [scroll]);

  const thumbSide: Side = layoutMode === "single" ? singleSide : view.side;
  const el = scroll.get(thumbSide);
  const thumbSize = el && el.scrollHeight > el.clientHeight ? el.clientHeight / el.scrollHeight : 1;
  const thumbTop = view.fraction * (1 - thumbSize);

  const rootRef = React.useRef<HTMLDivElement>(null);
  const dragging = React.useRef(false);

  const scrollTo = (clientY: number) => {
    const rect = rootRef.current?.getBoundingClientRect();
    if (!rect || effectiveMode !== "pixel") return;
    const f = Math.min(1, Math.max(0, (clientY - rect.top) / rect.height - thumbSize / 2) / Math.max(1e-6, 1 - thumbSize));
    scroll.scrollToFraction("old", f);
    scroll.scrollToFraction("new", f);
  };

  const laneColor = (c: ViewerChange, side: Side) => {
    if (selectedChangeId === c.id) {
      const primary = selectionSource === "old" || selectionSource === "new" ? selectionSource : "new";
      return side === primary ? MAP_SELECTED : MAP_LINKED;
    }
    if (c.kind === "moved" && showMoves) return markupFor(c, markup, showMoves).marker;
    return markupFor({ kind: side === "old" ? "removed" : "added", group: c.group }, markup, showMoves).marker;
  };

  const markers: { c: ViewerChange; side: Side; top: number; height: number }[] = [];
  for (const c of visibleChanges) {
    if (c.oldPos) markers.push({ c, side: "old", top: c.oldPos.top, height: c.oldPos.height });
    if (c.newPos) markers.push({ c, side: "new", top: c.newPos.top, height: c.newPos.height });
  }
  // selected markers last so they paint on top
  markers.sort((a, b) => Number(a.c.id === selectedChangeId) - Number(b.c.id === selectedChangeId));

  return (
    <div
      ref={rootRef}
      data-testid="center-change-map"
      title="Change map — click a marker to jump, drag the grey band to scroll"
      className="relative h-full w-full cursor-pointer select-none border-x border-border bg-white dark:bg-neutral-950"
      onPointerDown={(e) => {
        if ((e.target as HTMLElement).dataset.marker) return;
        dragging.current = true;
        (e.currentTarget as HTMLElement).setPointerCapture?.(e.pointerId);
        scrollTo(e.clientY);
      }}
      onPointerMove={(e) => dragging.current && scrollTo(e.clientY)}
      onPointerUp={() => (dragging.current = false)}
    >
      <div className="absolute inset-y-0 left-1/2 w-px bg-border" />
      {markers.map(({ c, side, top, height }) => (
        <button
          key={`${side}-${c.id}`}
          type="button"
          data-marker="1"
          data-change-id={c.id}
          data-side={side}
          data-selected={selectedChangeId === c.id || undefined}
          aria-label={`Change ${c.order} (${side === "old" ? "original" : "revised"})`}
          onClick={(e) => {
            e.stopPropagation();
            selectChange(c.id, "map");
          }}
          className="absolute"
          style={{
            left: side === "old" ? 0 : "50%",
            width: "50%",
            top: `${top * 100}%`,
            height: `max(${Math.max(height, MIN_HEIGHT) * 100}%, 2px)`,
            background: laneColor(c, side),
          }}
        />
      ))}
      {thumbSize < 1 && (
        <div
          data-testid="map-thumb"
          className="pointer-events-none absolute inset-x-0"
          style={{ top: `${thumbTop * 100}%`, height: `${thumbSize * 100}%`, background: "rgba(0,0,0,0.2)" }}
        />
      )}
    </div>
  );
}
