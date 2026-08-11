"use client";
import * as React from "react";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";

import { indexDocument, locate, normalize, type AnchorResult, type NodeText } from "./findingAnchor";
import { readBlocks, type SectionsRef } from "./SectionIdPlugin";
import { normalizeSeverity } from "@/lib/format";
import type { Violation } from "@/lib/types";

/** Compliance findings drawn onto the editable document.
 *
 * Deliberately NOT MarkNodes. Wrapping findings in marks would write them into
 * the editor state, which is the document — they would be saved as content and
 * exported into the approved DOCX. A highlight is a view of the document, not
 * part of it, so nothing here ever enters the editor: a located span is drawn
 * as absolutely positioned divs in a layer OUTSIDE the contentEditable,
 * measured from a DOM range, and everything else is a data attribute on an
 * element Lexical already rendered.
 *
 * Two states, because they claim different things:
 *   - a located span (exact / text anchor) is marked word-precisely — we know
 *     which words are flagged, so we say so;
 *   - a fingerprint-only anchor keeps the block treatment, dashed: the wording
 *     was edited, so all we honestly know is "somewhere in this paragraph".
 * A range whose measured text no longer matches what the finding quotes is
 * demoted to the block treatment rather than drawn in the wrong place.
 */
export function FindingDecorationsPlugin({
  violations,
  selectedViolationId,
  hoveredViolationId = null,
  sectionsRef,
  onSelect,
  onHover,
  onResolved,
  onPlaced,
}: {
  violations: Violation[];
  selectedViolationId: string | null;
  /** Highlighted from elsewhere (a margin bubble under the pointer). Applied
   * without re-measuring — hovering a card must not re-run the locate pass. */
  hoveredViolationId?: string | null;
  /** The live document's blocks with their content-derived ids, maintained by
   * SectionIdPlugin. Read rather than re-walked: one pass per update, shared. */
  sectionsRef?: SectionsRef;
  onSelect?: (id: string) => void;
  /** The finding under the pointer, or null. The marks are pointer-events:none,
   * so this is hit-tested against the measured rects like the click is. */
  onHover?: (id: string | null) => void;
  /** Reports which findings could not be located, so the rail can say so
   * instead of silently showing nothing. */
  onResolved?: (unlocated: Array<{ id: string; reason: string }>) => void;
  /** Where each located finding sits vertically, for anything drawn beside the
   * text (the margin bubbles). Reported from this pass rather than measured
   * again: a second measurement is a second answer, and a card that disagrees
   * with the mark it points at is worse than no card. */
  onPlaced?: (spots: FindingSpot[]) => void;
}) {
  const [editor] = useLexicalComposerContext();
  const layerRef = React.useRef<HTMLDivElement | null>(null);
  // Where the span marks ended up, in layer coordinates. The marks themselves
  // are pointer-events:none so they cannot swallow a keystroke or a caret
  // placement, so a click on a flagged phrase is resolved geometrically.
  const hitsRef = React.useRef<Array<{ id: string; rects: Box[] }>>([]);
  // Latest callbacks without making the decorate effect depend on their
  // identity — a parent re-render must not force a full re-decorate.
  const onSelectRef = React.useRef(onSelect);
  const onHoverRef = React.useRef(onHover);
  const onResolvedRef = React.useRef(onResolved);
  const onPlacedRef = React.useRef(onPlaced);
  const hoveredRef = React.useRef(hoveredViolationId);
  onSelectRef.current = onSelect;
  onHoverRef.current = onHover;
  onResolvedRef.current = onResolved;
  onPlacedRef.current = onPlaced;
  hoveredRef.current = hoveredViolationId;
  const scrolledToRef = React.useRef<string | null>(null);
  // Last reported placement, so a decorate that moved nothing (every keystroke
  // in an unaffected paragraph) does not re-render the bubbles.
  const placedSigRef = React.useRef("");

  React.useEffect(() => {
    const decorate = () => {
      const root = editor.getRootElement();
      const layer = layerRef.current;
      if (!root || !layer) return;

      // Blocks + their content ids from the section map. It walks the document
      // once per update and only re-reads what changed; falling back to a walk
      // here keeps this working if it is ever mounted without one.
      const nodes: NodeText[] = sectionsRef?.current ?? readBlocks(editor, EMPTY_CACHE);

      // An empty document is not a document whose findings cannot be located —
      // it is a document that has not arrived yet. The editor is empty for the
      // seconds its import takes, and reporting every finding as unlocatable in
      // that window told the reviewer their whole analysis had come unstuck.
      // Nothing is drawn and nothing is claimed until there is text to claim it
      // against.
      if (nodes.length === 0) {
        layer.replaceChildren();
        hitsRef.current = [];
        return;
      }

      // Clear previous decorations before re-applying; a finding that moved
      // must not leave its old paragraph looking flagged.
      root.querySelectorAll("[data-finding-id]").forEach((el) => {
        el.removeAttribute("data-finding-id");
        el.removeAttribute("data-finding-severity");
        el.removeAttribute("data-finding-selected");
        el.removeAttribute("data-finding-hover");
        el.removeAttribute("data-finding-anchor");
      });

      // Normalized and flattened ONCE for the whole pass. Every finding used to
      // rebuild the flattened document for itself, which made a keystroke cost
      // O(findings × document).
      const doc = indexDocument(nodes);

      // Measure everything, then write once. Interleaving the two would force
      // a reflow per finding on every keystroke.
      const origin = layer.getBoundingClientRect();
      const placed: Placed[] = [];
      const unlocated: Array<{ id: string; reason: string }> = [];
      for (const v of violations) {
        const result: AnchorResult = locate(v, doc);
        if (result.status === "unlocated") {
          unlocated.push({ id: v.id, reason: result.reason });
          continue;
        }
        const severity = normalizeSeverity(v.severity);
        const rects: Box[] = [];
        const blockEls: HTMLElement[] = [];
        // A finding can cover several blocks (a heading and the paragraph under
        // it, a run of bullets). Each block is measured on its own terms: the
        // words where they can be measured, the block where they cannot.
        for (const span of result.spans) {
          const el = editor.getElementByKey(span.nodeKey);
          if (!el) continue;
          // A fingerprint match knows the paragraph, not the words — measuring
          // a span from it would be inventing one.
          const measured =
            result.status === "fingerprint"
              ? null
              : measure(el, span.start, span.end, v.current_text, result.spans.length > 1, origin);
          if (measured && measured.length) rects.push(...measured);
          else blockEls.push(el);
        }
        if (rects.length === 0 && blockEls.length === 0) {
          unlocated.push({ id: v.id, reason: "paragraph is not rendered" });
          continue;
        }
        placed.push({ id: v.id, severity, rects, blockEls, blockKey: result.spans[0].nodeKey });
      }

      const frag = document.createDocumentFragment();
      const hits: Array<{ id: string; rects: Box[] }> = [];
      for (const p of placed) {
        const selected = p.id === selectedViolationId;
        const hovered = p.id === hoveredRef.current;
        for (const el of p.blockEls) {
          // One element can carry several findings; the most severe wins the
          // colour, and first-wins would otherwise hide a critical under a low.
          const existing = el.getAttribute("data-finding-severity");
          if (!existing || RANK[p.severity] > (RANK[existing] ?? 0)) {
            el.setAttribute("data-finding-severity", p.severity);
            el.setAttribute("data-finding-id", p.id);
          }
          el.setAttribute("data-finding-anchor", "paragraph");
          if (selected) {
            el.setAttribute("data-finding-selected", "true");
            el.setAttribute("data-finding-id", p.id);
          }
          if (hovered) el.setAttribute("data-finding-hover", "true");
        }
        if (p.rects.length === 0) continue;
        hits.push({ id: p.id, rects: p.rects });
        // One div per client rect: a flagged phrase that wraps across lines is
        // several rects, and one box around them all would cover whole lines
        // the finding never mentioned.
        for (const r of p.rects) {
          const mark = document.createElement("div");
          mark.className = "finding-span";
          mark.setAttribute("data-finding-ref", p.id);
          mark.setAttribute("data-finding-severity", p.severity);
          if (selected) mark.setAttribute("data-finding-selected", "true");
          if (hovered) mark.setAttribute("data-finding-hover", "true");
          mark.style.left = `${r.x}px`;
          mark.style.top = `${r.y}px`;
          mark.style.width = `${r.w}px`;
          mark.style.height = `${r.h}px`;
          frag.appendChild(mark);
        }
      }
      layer.replaceChildren(frag);
      hitsRef.current = hits;
      onResolvedRef.current?.(unlocated);

      if (onPlacedRef.current) {
        // A span's own top; a paragraph-only anchor gets the paragraph's, which
        // is the most the fingerprint match actually knows.
        const spots: FindingSpot[] = placed.map((p) => ({
          id: p.id,
          top: Math.min(
            ...p.rects.map((r) => r.y),
            ...p.blockEls.map((el) => el.getBoundingClientRect().top - origin.top)
          ),
          anchored: p.rects.length > 0,
          blockKey: p.blockKey,
        }));
        const signature = spots
          .map((s) => `${s.id}:${Math.round(s.top)}:${s.blockKey}:${s.anchored}`)
          .join("|");
        if (signature !== placedSigRef.current) {
          placedSigRef.current = signature;
          onPlacedRef.current(spots);
        }
      }

      // Scroll only when the selection itself changed. decorate() also runs on
      // every keystroke, and yanking the view back to the selected finding
      // while the reviewer types elsewhere is the panel-beside-the-document
      // problem in another costume.
      if (selectedViolationId && scrolledToRef.current !== selectedViolationId) {
        const target = placed.find((p) => p.id === selectedViolationId);
        const el = target?.blockEls[0] ?? editor.getElementByKey(target?.blockKey ?? "");
        el?.scrollIntoView({ behavior: "smooth", block: "center" });
      }
      scrolledToRef.current = selectedViolationId;
    };

    // Coalesce to one measure per frame: typing fires an update per keystroke
    // and a resize fires in bursts, and each decorate reads layout.
    let frame = 0;
    const schedule = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(decorate);
    };

    const stopUpdates = editor.registerUpdateListener(schedule);
    // Any reflow moves the text out from under its mark: a window resize, the
    // rail collapsing, an image or a webfont landing. One observer on the root
    // catches all of them, where a resize listener would miss the last three.
    const observer = new ResizeObserver(schedule);
    const stopRoot = editor.registerRootListener((rootEl, prevEl) => {
      if (prevEl) observer.unobserve(prevEl);
      if (rootEl) observer.observe(rootEl);
      schedule();
    });
    void document.fonts?.ready.then(schedule);

    return () => {
      cancelAnimationFrame(frame);
      stopUpdates();
      stopRoot();
      observer.disconnect();
    };
  }, [editor, violations, selectedViolationId, sectionsRef]);

  // Hover is painted straight onto the existing marks. Re-running the locate
  // pass to change one attribute would re-measure every finding each time the
  // pointer crosses a card.
  React.useEffect(() => {
    const root = editor.getRootElement();
    const layer = layerRef.current;
    for (const el of [
      ...(layer?.querySelectorAll("[data-finding-hover]") ?? []),
      ...(root?.querySelectorAll("[data-finding-hover]") ?? []),
    ]) {
      el.removeAttribute("data-finding-hover");
    }
    if (!hoveredViolationId) return;
    const id = cssEscape(hoveredViolationId);
    layer
      ?.querySelectorAll(`[data-finding-ref="${id}"]`)
      .forEach((el) => el.setAttribute("data-finding-hover", "true"));
    root
      ?.querySelectorAll(`[data-finding-id="${id}"]`)
      .forEach((el) => el.setAttribute("data-finding-hover", "true"));
  }, [editor, hoveredViolationId]);

  React.useEffect(() => {
    // The marks are pointer-events:none — typing and caret placement go
    // straight through them — so both the click that selects a finding and the
    // hover that highlights one are resolved against the measured rects rather
    // than the event target.
    const at = (e: MouseEvent): string | null => {
      const origin = layerRef.current?.getBoundingClientRect();
      if (origin) {
        const x = e.clientX - origin.left;
        const y = e.clientY - origin.top;
        const hit = hitsRef.current.find((h) =>
          h.rects.some((r) => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h)
        );
        if (hit) return hit.id;
      }
      const el = (e.target as HTMLElement | null)?.closest?.("[data-finding-id]");
      return el?.getAttribute("data-finding-id") ?? null;
    };

    const onClick = (e: MouseEvent) => {
      const id = at(e);
      if (id) onSelectRef.current?.(id);
    };
    let last: string | null = null;
    const onMove = (e: MouseEvent) => {
      const id = at(e);
      if (id === last) return;
      last = id;
      onHoverRef.current?.(id);
    };
    const onLeave = () => {
      if (last === null) return;
      last = null;
      onHoverRef.current?.(null);
    };

    // Root listener rather than a one-shot getRootElement(): it fires with the
    // current root immediately, again if Lexical swaps it, and with null on
    // teardown, so the listener cannot be attached to a dead element.
    return editor.registerRootListener((rootEl, prevEl) => {
      prevEl?.removeEventListener("click", onClick);
      prevEl?.removeEventListener("mousemove", onMove);
      prevEl?.removeEventListener("mouseleave", onLeave);
      rootEl?.addEventListener("click", onClick);
      rootEl?.addEventListener("mousemove", onMove);
      rootEl?.addEventListener("mouseleave", onLeave);
    });
  }, [editor]);

  // Sibling of the contentEditable, never inside it: Lexical reconciles its own
  // subtree and foreign DOM in there is asking to be overwritten — or worse,
  // parsed back as content. Zero-sized, so it is pure coordinate origin.
  return <div ref={layerRef} className="finding-overlay-layer" aria-hidden="true" />;
}

const EMPTY_CACHE = new Map<string, never>();

/** CSS.escape with a fallback: ids are UUIDs, so the fallback is only ever
 * reached in a browser old enough that the escape is moot. */
function cssEscape(value: string): string {
  return typeof CSS !== "undefined" && CSS.escape ? CSS.escape(value) : value.replace(/"/g, '\\"');
}

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** A located finding's vertical position in the sheet's coordinates. */
export interface FindingSpot {
  id: string;
  top: number;
  /** True when the exact words were located; false when only the paragraph
   * was, so anything drawn from it can say which claim it is making. */
  anchored: boolean;
  /** The block the finding starts in — what the margin cards cluster by, so
   * several findings on one paragraph become one card instead of a stack that
   * drifts away from the text. */
  blockKey: string;
}

interface Placed {
  id: string;
  severity: string;
  /** Word-precise rects, in layer coordinates. */
  rects: Box[];
  /** Blocks marked whole, because their words could not be measured. */
  blockEls: HTMLElement[];
  blockKey: string;
}

/** Client rects for [start, end) of `el`'s text, in layer coordinates.
 *
 * The offsets are indexes into the block's NORMALIZED text, which agrees with
 * the DOM's text nodes for prose but not for everything — a list joins its
 * items with a newline the DOM has no character for, and a paragraph with
 * double spaces is shorter normalized than rendered. So the range is checked
 * against the words the finding actually quotes before it is drawn, and a
 * mismatch returns null to fall back to the block treatment. A mark on the
 * wrong words tells a reviewer that compliant text is a violation, which is
 * worse than no mark at all.
 *
 * `partial` relaxes that check to containment, for a finding whose quote runs
 * across several blocks: no single block holds all of it, so equality would
 * reject every block of every straddling finding.
 *
 * Returns [] rather than null when the text is laid out but invisible (a
 * collapsed or display:none ancestor) — nothing to draw, nothing misplaced.
 */
function measure(
  el: HTMLElement,
  start: number,
  end: number,
  quoted: string | null | undefined,
  partial: boolean,
  origin: DOMRect
): Box[] | null {
  if (!(end > start)) return null;
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT);
  const range = document.createRange();
  let at = 0;
  let anchored = false;
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    const len = n.textContent?.length ?? 0;
    if (!anchored && at + len >= start) {
      range.setStart(n, start - at);
      anchored = true;
    }
    if (anchored && at + len >= end) {
      range.setEnd(n, end - at);
      break;
    }
    at += len;
  }
  // Never anchored, or the text ran out before `end` — the range is collapsed
  // and drawing it would mark a caret-width sliver of the wrong words.
  if (!anchored || range.collapsed) return null;
  const span = (quoted ?? "").trim();
  if (span) {
    const measured = normalize(range.toString());
    const wanted = normalize(span);
    if (partial ? !measured || !wanted.includes(measured) : measured !== wanted) return null;
  }
  return Array.from(range.getClientRects())
    .filter((r) => r.width > 0 && r.height > 0)
    .map((r) => ({ x: r.left - origin.left, y: r.top - origin.top, w: r.width, h: r.height }));
}

const RANK: Record<string, number> = { low: 1, medium: 2, high: 3, critical: 4 };
