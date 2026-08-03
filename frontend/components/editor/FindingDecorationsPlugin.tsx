"use client";
import * as React from "react";
import { $getRoot } from "lexical";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";

import { locate, normalize, type AnchorResult, type NodeText } from "./findingAnchor";
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
  onSelect,
  onResolved,
}: {
  violations: Violation[];
  selectedViolationId: string | null;
  onSelect?: (id: string) => void;
  /** Reports which findings could not be located, so the rail can say so
   * instead of silently showing nothing. */
  onResolved?: (unlocated: Array<{ id: string; reason: string }>) => void;
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
  const onResolvedRef = React.useRef(onResolved);
  onSelectRef.current = onSelect;
  onResolvedRef.current = onResolved;
  const scrolledToRef = React.useRef<string | null>(null);

  React.useEffect(() => {
    const decorate = () => {
      const root = editor.getRootElement();
      const layer = layerRef.current;
      if (!root || !layer) return;

      const nodes: NodeText[] = [];
      editor.getEditorState().read(() => {
        for (const child of $getRoot().getChildren()) {
          const text = child.getTextContent();
          if (text.trim()) nodes.push({ key: child.getKey(), text });
        }
      });

      // Clear previous decorations before re-applying; a finding that moved
      // must not leave its old paragraph looking flagged.
      root.querySelectorAll("[data-finding-id]").forEach((el) => {
        el.removeAttribute("data-finding-id");
        el.removeAttribute("data-finding-severity");
        el.removeAttribute("data-finding-selected");
        el.removeAttribute("data-finding-anchor");
      });

      // Measure everything, then write once. Interleaving the two would force
      // a reflow per finding on every keystroke.
      const origin = layer.getBoundingClientRect();
      const placed: Placed[] = [];
      const unlocated: Array<{ id: string; reason: string }> = [];
      for (const v of violations) {
        const result: AnchorResult = locate(v, nodes);
        if (result.status === "unlocated") {
          unlocated.push({ id: v.id, reason: result.reason });
          continue;
        }
        const el = editor.getElementByKey(result.nodeKey);
        if (!el) {
          unlocated.push({ id: v.id, reason: "paragraph is not rendered" });
          continue;
        }
        const severity = normalizeSeverity(v.severity);
        // A fingerprint match knows the paragraph, not the words — measuring a
        // span from it would be inventing one.
        const rects =
          result.status === "fingerprint"
            ? null
            : measure(el, result.start, result.end, v.current_text, origin);
        placed.push(
          rects && rects.length
            ? { kind: "span", id: v.id, severity, el, rects }
            : { kind: "block", id: v.id, severity, el }
        );
      }

      const frag = document.createDocumentFragment();
      const hits: Array<{ id: string; rects: Box[] }> = [];
      for (const p of placed) {
        const selected = p.id === selectedViolationId;
        if (p.kind === "block") {
          // One element can carry several findings; the most severe wins the
          // colour, and first-wins would otherwise hide a critical under a low.
          const existing = p.el.getAttribute("data-finding-severity");
          if (!existing || RANK[p.severity] > (RANK[existing] ?? 0)) {
            p.el.setAttribute("data-finding-severity", p.severity);
            p.el.setAttribute("data-finding-id", p.id);
          }
          p.el.setAttribute("data-finding-anchor", "paragraph");
          if (selected) {
            p.el.setAttribute("data-finding-selected", "true");
            p.el.setAttribute("data-finding-id", p.id);
          }
          continue;
        }
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

      // Scroll only when the selection itself changed. decorate() also runs on
      // every keystroke, and yanking the view back to the selected finding
      // while the reviewer types elsewhere is the panel-beside-the-document
      // problem in another costume.
      if (selectedViolationId && scrolledToRef.current !== selectedViolationId) {
        placed
          .find((p) => p.id === selectedViolationId)
          ?.el.scrollIntoView({ behavior: "smooth", block: "center" });
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
  }, [editor, violations, selectedViolationId]);

  React.useEffect(() => {
    const onClick = (e: MouseEvent) => {
      // The marks are pointer-events:none — typing and caret placement go
      // straight through them — so the click that selects a finding is
      // resolved against the measured rects rather than the event target.
      const origin = layerRef.current?.getBoundingClientRect();
      if (origin) {
        const x = e.clientX - origin.left;
        const y = e.clientY - origin.top;
        const hit = hitsRef.current.find((h) =>
          h.rects.some((r) => x >= r.x && x <= r.x + r.w && y >= r.y && y <= r.y + r.h)
        );
        if (hit) {
          onSelectRef.current?.(hit.id);
          return;
        }
      }
      const el = (e.target as HTMLElement | null)?.closest?.("[data-finding-id]");
      const id = el?.getAttribute("data-finding-id");
      if (id) onSelectRef.current?.(id);
    };
    // Root listener rather than a one-shot getRootElement(): it fires with the
    // current root immediately, again if Lexical swaps it, and with null on
    // teardown, so the listener cannot be attached to a dead element.
    return editor.registerRootListener((rootEl, prevEl) => {
      prevEl?.removeEventListener("click", onClick);
      rootEl?.addEventListener("click", onClick);
    });
  }, [editor]);

  // Sibling of the contentEditable, never inside it: Lexical reconciles its own
  // subtree and foreign DOM in there is asking to be overwritten — or worse,
  // parsed back as content. Zero-sized, so it is pure coordinate origin.
  return <div ref={layerRef} className="finding-overlay-layer" aria-hidden="true" />;
}

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

type Placed =
  | { kind: "span"; id: string; severity: string; el: HTMLElement; rects: Box[] }
  | { kind: "block"; id: string; severity: string; el: HTMLElement };

/** Client rects for [start, end) of `el`'s text, in layer coordinates.
 *
 * The offsets are indexes into Lexical's getTextContent(), which agrees with
 * the DOM's text nodes for prose but not for everything — a list joins its
 * items with a newline the DOM has no character for. So the range is checked
 * against the words the finding actually quotes before it is drawn, and a
 * mismatch returns null to fall back to the block treatment. A mark on the
 * wrong words tells a reviewer that compliant text is a violation, which is
 * worse than no mark at all.
 *
 * Returns [] rather than null when the text is laid out but invisible (a
 * collapsed or display:none ancestor) — nothing to draw, nothing misplaced.
 */
function measure(
  el: HTMLElement,
  start: number,
  end: number,
  quoted: string | null | undefined,
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
  if (span && normalize(range.toString()) !== normalize(span)) return null;
  return Array.from(range.getClientRects())
    .filter((r) => r.width > 0 && r.height > 0)
    .map((r) => ({ x: r.left - origin.left, y: r.top - origin.top, w: r.width, h: r.height }));
}

const RANK: Record<string, number> = { low: 1, medium: 2, high: 3, critical: 4 };
