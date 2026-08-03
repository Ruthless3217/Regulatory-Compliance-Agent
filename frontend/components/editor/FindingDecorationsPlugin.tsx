"use client";
import * as React from "react";
import { $getRoot } from "lexical";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";

import { locate, type AnchorResult, type NodeText } from "./findingAnchor";
import { normalizeSeverity } from "@/lib/format";
import type { Violation } from "@/lib/types";

/** Compliance findings drawn onto the editable document.
 *
 * Deliberately NOT MarkNodes. Wrapping findings in marks would write them into
 * the editor state, which is the document — they would be saved as content and
 * exported into the approved DOCX. A highlight is a view of the document, not
 * part of it, so this only ever sets classes and data attributes on the DOM
 * elements Lexical already rendered. The editor state is never touched, and
 * nothing here can change what export produces.
 *
 * Highlighting is block-level: the paragraph carrying the finding is marked,
 * not the exact words. Span-precise underlines need overlay rects measured
 * from DOM ranges, and block-level already delivers what a reviewer needs —
 * find it, click it, scroll to it. Upgrade if reviewers ask to see the exact
 * words while editing.
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
  // Latest callbacks without making the decorate effect depend on their
  // identity — a parent re-render must not force a full re-decorate.
  const onSelectRef = React.useRef(onSelect);
  const onResolvedRef = React.useRef(onResolved);
  onSelectRef.current = onSelect;
  onResolvedRef.current = onResolved;

  React.useEffect(() => {
    const decorate = () => {
      const nodes: NodeText[] = [];
      editor.getEditorState().read(() => {
        for (const child of $getRoot().getChildren()) {
          const text = child.getTextContent();
          if (text.trim()) nodes.push({ key: child.getKey(), text });
        }
      });

      // Clear previous decorations before re-applying; a finding that moved
      // must not leave its old paragraph looking flagged.
      const root = editor.getRootElement();
      if (!root) return;
      root.querySelectorAll("[data-finding-id]").forEach((el) => {
        el.removeAttribute("data-finding-id");
        el.removeAttribute("data-finding-severity");
        el.removeAttribute("data-finding-selected");
      });

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
        // One element can carry several findings; the most severe wins the
        // colour, and first-wins would otherwise hide a critical under a low.
        const existing = el.getAttribute("data-finding-severity");
        const severity = normalizeSeverity(v.severity);
        if (!existing || RANK[severity] > (RANK[existing] ?? 0)) {
          el.setAttribute("data-finding-severity", severity);
          el.setAttribute("data-finding-id", v.id);
        }
        if (v.id === selectedViolationId) {
          el.setAttribute("data-finding-selected", "true");
          el.setAttribute("data-finding-id", v.id);
          el.scrollIntoView({ behavior: "smooth", block: "center" });
        }
      }
      onResolvedRef.current?.(unlocated);
    };

    decorate();
    // Re-decorate on every document change: an edit can move, merge or delete
    // the paragraph a finding was anchored to.
    return editor.registerUpdateListener(() => decorate());
  }, [editor, violations, selectedViolationId]);

  React.useEffect(() => {
    const root = editor.getRootElement();
    if (!root) return;
    const onClick = (e: MouseEvent) => {
      const el = (e.target as HTMLElement | null)?.closest?.("[data-finding-id]");
      const id = el?.getAttribute("data-finding-id");
      if (id) onSelectRef.current?.(id);
    };
    root.addEventListener("click", onClick);
    return () => root.removeEventListener("click", onClick);
  }, [editor]);

  return null;
}

const RANK: Record<string, number> = { low: 1, medium: 2, high: 3, critical: 4 };
