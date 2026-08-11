"use client";
import * as React from "react";

import { Badge, SeverityBadge } from "@/components/ui/badge";
import { ActionTags } from "@/components/violation/ActionTags";
import { locationLine, ruleLine, verdictLabel } from "@/components/review/ViolationCard";
import { categoryLabel, normalizeSeverity } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { FindingSpot } from "./FindingDecorationsPlugin";
import type { Violation } from "@/lib/types";

/**
 * Findings as cards in the document's right margin, each level with the text it
 * describes.
 *
 * A rail forces the reviewer to match a list entry against a highlight by eye;
 * a margin card is already beside its own sentence, which is how every review
 * tool that has solved this (Word's review pane, Docs comments, a PR diff)
 * presents it. Split mode has no rails at all, and Edit collapses its rail by
 * default, so in both the cards are the findings.
 *
 * Positions come from `FindingDecorationsPlugin` — the pass that draws the
 * marks — so a card and its mark cannot disagree. Cards never overlap: they are
 * laid out top-down and pushed past the one above, so a card can sit BELOW its
 * mark, never above it. That drift is what the leader lines are for, and why
 * findings on one block are one card rather than a stack: the fewer cards, the
 * less the column has to slide to fit them.
 */

/** The card column, and the gutter between it and the text. Exported because
 * the sheet has to reserve exactly this much room on its right — the two used
 * to be written out separately in two files, in different units, and drifted. */
export const BUBBLE_WIDTH = 220;
export const BUBBLE_GUTTER = 16;
export const BUBBLE_LANE = BUBBLE_WIDTH + BUBBLE_GUTTER;

const GAP = 8;
/** Used only before a card has been measured, on the very first paint. */
const ASSUMED_HEIGHT = 72;
/** Where on the card the leader line lands — roughly its first line of text. */
const LEADER_INSET = 12;

const TONE: Record<string, string> = {
  critical: "border-l-sev-critical",
  high: "border-l-sev-high",
  medium: "border-l-sev-medium",
  low: "border-l-sev-low",
};

const RANK: Record<string, number> = { low: 1, medium: 2, high: 3, critical: 4 };

interface Cluster {
  /** The block every finding in it is anchored to. */
  key: string;
  top: number;
  findings: Array<{ violation: Violation; spot: FindingSpot }>;
}

export function FindingBubbles({
  violations,
  spots,
  selectedViolationId,
  hoveredViolationId = null,
  onSelect,
  onHover,
}: {
  violations: Violation[];
  spots: FindingSpot[];
  selectedViolationId: string | null;
  hoveredViolationId?: string | null;
  onSelect?: (id: string) => void;
  /** Pointer on a card — the document marks the same finding, so the pair reads
   * as one thing rather than two lists that happen to share a colour. */
  onHover?: (id: string | null) => void;
}) {
  const byId = React.useMemo(() => new Map(violations.map((v) => [v.id, v])), [violations]);

  // Only findings that were actually located get a card. An unlocated one has
  // no place on the page to point at, and ReviewTab already says so in a banner
  // — inventing a position for it here would be the wrong-sentence failure the
  // anchoring is careful to avoid.
  const clusters = React.useMemo(() => {
    const groups = new Map<string, Cluster>();
    for (const spot of spots) {
      const violation = byId.get(spot.id);
      if (!violation || violation.suppressed) continue;
      const group = groups.get(spot.blockKey) ?? { key: spot.blockKey, top: spot.top, findings: [] };
      group.top = Math.min(group.top, spot.top);
      group.findings.push({ violation, spot });
      groups.set(spot.blockKey, group);
    }
    for (const group of groups.values()) {
      // Worst first: the card shows its head finding when collapsed, and a
      // "low" standing in front of a "critical" hides the one that blocks
      // approval.
      group.findings.sort(
        (a, b) =>
          RANK[normalizeSeverity(b.violation.severity)] -
            RANK[normalizeSeverity(a.violation.severity)] || a.spot.top - b.spot.top
      );
    }
    return [...groups.values()].sort((a, b) => a.top - b.top);
  }, [spots, byId]);

  const [openKey, setOpenKey] = React.useState<string | null>(null);
  const refs = React.useRef<Array<HTMLDivElement | null>>([]);
  const [tops, setTops] = React.useState<number[]>([]);

  // A selected finding always shows: selecting one in the rail and watching its
  // card stay folded inside a cluster is the same "nothing happened" failure
  // the rail itself has.
  const selectedKey = React.useMemo(
    () => clusters.find((c) => c.findings.some((f) => f.violation.id === selectedViolationId))?.key ?? null,
    [clusters, selectedViolationId]
  );

  // Stack them after paint, once each card's real height is known. Selection
  // and expansion are dependencies because both unfold a card, and everything
  // below it has to move down by that much.
  React.useLayoutEffect(() => {
    let bottom = -Infinity;
    const next = clusters.map((cluster, i) => {
      const height = refs.current[i]?.offsetHeight ?? ASSUMED_HEIGHT;
      const top = Math.max(cluster.top, bottom + GAP);
      bottom = top + height;
      return top;
    });
    setTops((prev) =>
      prev.length === next.length && prev.every((t, i) => t === next[i]) ? prev : next
    );
  }, [clusters, selectedViolationId, openKey, selectedKey]);

  if (clusters.length === 0) return null;

  return (
    // Hangs off the sheet's right edge, in the sheet's own coordinate space —
    // the same origin the plugin measured against, so `top` needs no fixing up.
    <div
      className="absolute left-full top-0"
      style={{ width: BUBBLE_WIDTH, marginLeft: BUBBLE_GUTTER }}
      aria-label="Findings"
    >
      {/* Leader lines, drawn back across the gutter to the mark each card
          describes. Without them a card pushed down past three others is just a
          card near some text. Zero-sized and overflowing, so it lays out
          nothing and catches no pointer. */}
      <svg
        className="pointer-events-none absolute overflow-visible text-border"
        style={{ left: -BUBBLE_GUTTER, top: 0, width: BUBBLE_GUTTER, height: 0 }}
        aria-hidden="true"
      >
        {clusters.map((cluster, i) => {
          const top = tops[i] ?? cluster.top;
          const active =
            cluster.key === selectedKey ||
            cluster.findings.some((f) => f.violation.id === hoveredViolationId);
          return (
            <path
              key={cluster.key}
              d={`M 0 ${cluster.top} H ${BUBBLE_GUTTER * 0.45} V ${top + LEADER_INSET} H ${BUBBLE_GUTTER}`}
              fill="none"
              stroke="currentColor"
              strokeWidth={active ? 1.5 : 1}
              className={active ? "text-primary" : undefined}
            />
          );
        })}
      </svg>

      {clusters.map((cluster, i) => {
        const expanded = cluster.key === openKey || cluster.key === selectedKey;
        const shown = expanded ? cluster.findings : cluster.findings.slice(0, 1);
        const hidden = cluster.findings.length - shown.length;
        return (
          <div
            key={cluster.key}
            ref={(el) => {
              refs.current[i] = el;
            }}
            className="absolute left-0 w-full transition-[top] duration-150"
            style={{ top: tops[i] ?? cluster.top }}
          >
            <div className="space-y-1">
              {shown.map(({ violation, spot }) => (
                <BubbleCard
                  key={violation.id}
                  violation={violation}
                  anchored={spot.anchored}
                  selected={violation.id === selectedViolationId}
                  hovered={violation.id === hoveredViolationId}
                  onSelect={() => onSelect?.(violation.id)}
                  onHover={onHover}
                />
              ))}
              {hidden > 0 && (
                <button
                  type="button"
                  onClick={() => setOpenKey(cluster.key)}
                  className="w-full rounded-sm border border-dashed border-border bg-background/80 px-2 py-1 text-left text-[11px] text-muted-foreground hover:border-primary/40 hover:text-foreground"
                >
                  +{hidden} more on this block
                </button>
              )}
              {expanded && cluster.findings.length > 1 && cluster.key === openKey && (
                <button
                  type="button"
                  onClick={() => setOpenKey(null)}
                  className="w-full px-2 text-left text-[11px] text-muted-foreground hover:text-foreground"
                >
                  Collapse
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/** One finding as a margin card.
 *
 * Shows what the reviewer needs to judge it from here — severity, verdict,
 * where it is, what it cites, what it asks for — and nothing that would fork
 * the rail card's logic. Applying a fix, recording a verdict and rewriting all
 * stay in ViolationCard: clicking this selects the finding, which opens the
 * rail on it (ReviewTab.selectViolation), so there is one implementation of
 * each action rather than two that can disagree.
 */
function BubbleCard({
  violation,
  anchored,
  selected,
  hovered,
  onSelect,
  onHover,
}: {
  violation: Violation;
  anchored: boolean;
  selected: boolean;
  hovered: boolean;
  onSelect: () => void;
  onHover?: (id: string | null) => void;
}) {
  const severity = normalizeSeverity(violation.severity);
  const verdict = verdictLabel(violation);
  const where = locationLine(violation);
  const cites = ruleLine(violation);
  return (
    <div
      onMouseEnter={() => onHover?.(violation.id)}
      onMouseLeave={() => onHover?.(null)}
      className={cn(
        "rounded-sm border border-l-[3px] border-border bg-background p-2 shadow-sm",
        "transition-colors hover:border-primary/40",
        TONE[severity] ?? TONE.low,
        hovered && !selected && "border-primary/40 bg-muted/40",
        selected && "border-primary bg-primary-50/40 ring-1 ring-primary"
      )}
    >
      <button
        type="button"
        onClick={onSelect}
        onFocus={() => onHover?.(violation.id)}
        onBlur={() => onHover?.(null)}
        aria-pressed={selected}
        className="block w-full text-left"
      >
        <span className="mb-1 flex flex-wrap items-center gap-1">
          <SeverityBadge severity={severity} />
          {verdict !== "open" && <Badge tone="default">{verdict}</Badge>}
          {violation.fix_applied && <Badge tone="success">applied</Badge>}
        </span>
        <span className="micro-label mb-1 block text-muted-foreground">
          {categoryLabel(violation.category)}
          {/* The paragraph-only anchor is stated, not hidden: the card is beside
              the right paragraph but the exact words are no longer known, and a
              reviewer must not read it as word-precise. */}
          {!anchored && " · in this block"}
        </span>
        <span
          className={cn(
            "block text-[12px] leading-snug text-foreground",
            !selected && "line-clamp-3"
          )}
        >
          {violation.description}
        </span>
      </button>
      {selected && (
        <div>
          {(where || cites) && (
            <p className="mt-1.5 font-mono text-[10px] leading-snug text-muted-foreground">
              {[where, cites].filter(Boolean).join(" · ")}
            </p>
          )}
          <ActionTags violation={violation} className="mt-1.5 flex flex-wrap items-center gap-1" />
          {violation.reviewer_comment && (
            <p className="mt-1.5 rounded-sm border border-border p-1.5 text-[11px] leading-snug text-muted-foreground">
              {violation.reviewer_comment}
            </p>
          )}
          {violation.suggested_fix && (
            <p className="mt-1.5 rounded-sm bg-primary-50/60 p-1.5 text-[11px] leading-snug">
              <span className="micro-label block text-muted-foreground">suggested</span>
              {violation.suggested_fix}
            </p>
          )}
        </div>
      )}
    </div>
  );
}
