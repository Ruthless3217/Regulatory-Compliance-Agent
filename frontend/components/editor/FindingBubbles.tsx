"use client";
import * as React from "react";

import { categoryLabel, normalizeSeverity } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { FindingSpot } from "./FindingDecorationsPlugin";
import type { Violation } from "@/lib/types";

/**
 * Findings as cards in the document's right margin, each level with the text it
 * describes.
 *
 * This is what replaces the findings rail in split mode, where both rails are
 * hidden to give the two documents the width. A rail forces the reviewer to
 * match a list entry against a highlight by eye; a margin card is already
 * beside its own sentence, which is how every review tool that has solved this
 * (Word's review pane, Docs comments, a PR diff) presents it.
 *
 * Positions come from `FindingDecorationsPlugin` — the pass that draws the
 * marks — so a card and its mark cannot disagree. Cards never overlap: they are
 * laid out top-down and pushed past the one above, so a card can sit BELOW its
 * mark, never above it. That is the standard compromise, and the reason the
 * selected card is also outlined in the text: the line is the truth, the card
 * is the label.
 */

const GAP = 8;
/** Used only before a card has been measured, on the very first paint. */
const ASSUMED_HEIGHT = 72;

const TONE: Record<string, string> = {
  critical: "border-l-sev-critical",
  high: "border-l-sev-high",
  medium: "border-l-sev-medium",
  low: "border-l-sev-low",
};

export function FindingBubbles({
  violations,
  spots,
  selectedViolationId,
  onSelect,
}: {
  violations: Violation[];
  spots: FindingSpot[];
  selectedViolationId: string | null;
  onSelect?: (id: string) => void;
}) {
  const byId = React.useMemo(
    () => new Map(violations.map((v) => [v.id, v])),
    [violations]
  );

  // Only findings that were actually located get a card. An unlocated one has
  // no place on the page to point at, and ReviewTab already says so in a banner
  // — inventing a position for it here would be the wrong-sentence failure the
  // anchoring is careful to avoid.
  const cards = React.useMemo(
    () =>
      spots
        .map((spot) => ({ spot, violation: byId.get(spot.id) }))
        .filter((c): c is { spot: FindingSpot; violation: Violation } =>
          Boolean(c.violation && !c.violation.suppressed)
        )
        .sort((a, b) => a.spot.top - b.spot.top),
    [spots, byId]
  );

  const refs = React.useRef<Array<HTMLDivElement | null>>([]);
  const [tops, setTops] = React.useState<number[]>([]);

  // Stack them after paint, once each card's real height is known. Selection is
  // a dependency because the selected card unfolds its suggested fix, and
  // everything below it has to move down by that much.
  React.useLayoutEffect(() => {
    let bottom = -Infinity;
    const next = cards.map((card, i) => {
      const height = refs.current[i]?.offsetHeight ?? ASSUMED_HEIGHT;
      const top = Math.max(card.spot.top, bottom + GAP);
      bottom = top + height;
      return top;
    });
    setTops((prev) =>
      prev.length === next.length && prev.every((t, i) => t === next[i]) ? prev : next
    );
  }, [cards, selectedViolationId]);

  if (cards.length === 0) return null;

  return (
    // Hangs off the sheet's right edge, in the sheet's own coordinate space —
    // the same origin the plugin measured against, so `top` needs no fixing up.
    // Width + gutter must match the sheet's reserved right margin in
    // LexicalDocument, or the cards overhang the pane.
    <div className="absolute left-full top-0 ml-4 w-[220px]" aria-label="Findings">
      {cards.map((card, i) => {
        const { violation, spot } = card;
        const selected = violation.id === selectedViolationId;
        const severity = normalizeSeverity(violation.severity);
        return (
          <div
            key={violation.id}
            ref={(el) => {
              refs.current[i] = el;
            }}
            className="absolute left-0 w-full transition-[top] duration-150"
            style={{ top: tops[i] ?? spot.top }}
          >
            <button
              type="button"
              onClick={() => onSelect?.(violation.id)}
              aria-pressed={selected}
              className={cn(
                "w-full rounded-sm border border-l-[3px] border-border bg-background p-2 text-left shadow-sm",
                "hover:border-primary/40",
                TONE[severity] ?? TONE.low,
                selected && "border-primary ring-1 ring-primary"
              )}
            >
              <span className="micro-label mb-1 block text-muted-foreground">
                {categoryLabel(violation.category)} · {severity}
                {/* The paragraph-only anchor is stated, not hidden: the card is
                    beside the right paragraph but the exact words are no longer
                    known, and a reviewer must not read it as word-precise. */}
                {!spot.anchored && " · in this paragraph"}
              </span>
              <span
                className={cn(
                  "block text-[12px] leading-snug text-foreground",
                  !selected && "line-clamp-3"
                )}
              >
                {violation.description}
              </span>
              {selected && violation.suggested_fix && (
                <span className="mt-1.5 block rounded-sm bg-primary-50/60 p-1.5 text-[11px] leading-snug">
                  <span className="micro-label block text-muted-foreground">suggested</span>
                  {violation.suggested_fix}
                </span>
              )}
            </button>
          </div>
        );
      })}
    </div>
  );
}
