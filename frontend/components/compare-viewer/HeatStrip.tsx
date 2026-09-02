"use client";
import * as React from "react";
import { cn } from "@/lib/utils";
import { useViewer, type Side } from "./ViewerContext";

const barColor = (kind: string) =>
  kind === "removed"
    ? "bg-sev-critical"
    : kind === "added"
    ? "bg-success"
    : kind === "moved"
    ? "bg-violet-600"
    : "bg-primary";

/**
 * 12px vertical minimap between the panes: one bar per change positioned by its
 * document fraction, a viewport band fed by the active pane's scroll fraction,
 * and click-to-jump to the nearest change.
 */
export function HeatStrip() {
  const {
    effectiveMode,
    showMoves,
    selectedChangeId,
    setSelectedChangeId,
    scroll,
    singleSide,
    layoutMode,
    viewerChanges: changes,
  } = useViewer();

  const [viewport, setViewport] = React.useState<{ fraction: number; side: Side }>({
    fraction: 0,
    side: "new",
  });
  React.useEffect(() => scroll.subscribe((fraction, side) => setViewport({ fraction, side })), [scroll]);

  // Viewport band size ≈ visible window / total scrollable height of the active pane.
  const bandSide = layoutMode === "single" ? singleSide : viewport.side;
  const activeEl = scroll.get(bandSide);
  const bandFrac =
    activeEl && activeEl.scrollHeight > activeEl.clientHeight
      ? activeEl.clientHeight / activeEl.scrollHeight
      : 1;
  const bandTop = viewport.fraction * (1 - bandFrac);

  const rootRef = React.useRef<HTMLDivElement>(null);
  const onClick = (e: React.MouseEvent) => {
    if (changes.length === 0) return;
    const rect = rootRef.current?.getBoundingClientRect();
    if (!rect) return;
    const frac = Math.min(1, Math.max(0, (e.clientY - rect.top) / rect.height));
    let nearest = changes[0];
    let best = Infinity;
    for (const c of changes) {
      const d = Math.abs(c.fraction - frac);
      if (d < best) {
        best = d;
        nearest = c;
      }
    }
    setSelectedChangeId(nearest.id);
    if (effectiveMode === "pixel") {
      scroll.scrollToFraction("old", nearest.fraction);
      scroll.scrollToFraction("new", nearest.fraction);
    }
  };

  return (
    <div
      ref={rootRef}
      onClick={onClick}
      title="Change map — click to jump"
      className="relative h-full w-full cursor-pointer border-x border-border bg-muted/30"
    >
      {/* viewport band */}
      {bandFrac < 1 && (
        <div
          className="absolute inset-x-0 bg-foreground/10"
          style={{ top: `${bandTop * 100}%`, height: `${bandFrac * 100}%` }}
        />
      )}
      {/* change bars */}
      {changes.map((c) => {
        const dim = c.kind === "moved" && !showMoves;
        return (
          <div
            key={c.id}
            className={cn(
              "absolute inset-x-[2px] h-[2px] rounded-full",
              barColor(c.kind),
              dim && "opacity-20",
              selectedChangeId === c.id && "inset-x-0 h-[3px] ring-1 ring-primary"
            )}
            style={{ top: `${c.fraction * 100}%` }}
          />
        );
      })}
    </div>
  );
}
