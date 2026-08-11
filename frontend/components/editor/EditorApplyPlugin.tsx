"use client";
import * as React from "react";
import {
  $createParagraphNode,
  $createTextNode,
  $getNodeByKey,
  $getRoot,
  $isElementNode,
  type LexicalEditor,
} from "lexical";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";

import { indexDocument, locate, normalize, type NodeText } from "./findingAnchor";
import { readBlocks, type SectionsRef } from "./SectionIdPlugin";
import type { Violation } from "@/lib/types";

/** Applying a reviewer's fix to the document the reviewer can see.
 *
 * Apply-fix used to splice the plain-text projection of the document and post
 * it alongside the editor's UNTOUCHED lexical state and HTML. The backend
 * stored both, the export renders from the HTML, and so an approved DOCX
 * shipped the original wording with `fix_applied = true` against it while the
 * editor on screen never showed the change. Two views of one document, saved in
 * one request, disagreeing about what the document says.
 *
 * So when the rich editor is the working surface, the fix is made IN it: the
 * finding is located with the same anchoring the highlights use, the span is
 * replaced through a Lexical update, and the normal onChange path emits state,
 * HTML and text from that one edited document. There is no second copy to
 * disagree with.
 *
 * When the span cannot be found the edit is refused outright. Splicing text we
 * could not locate in the editor is how the two views came apart in the first
 * place.
 */
export type EditorFixMode = "replace" | "append";

export type EditorFixApply = (
  violation: Violation,
  replacement: string,
  mode: EditorFixMode
) => Promise<
  { ok: true; text: string } | { ok: false; reason: "unlocated" | "already-present" }
>;

export function EditorApplyPlugin({
  register,
  sectionsRef,
}: {
  /** Handed to the workspace so Apply-fix (which lives in the findings rail,
   * outside this editor) can route through it. Called with null on unmount, so
   * a card can always tell whether an editor is actually on screen. */
  register: (apply: EditorFixApply | null) => void;
  sectionsRef?: SectionsRef;
}) {
  const [editor] = useLexicalComposerContext();

  React.useEffect(() => {
    const apply: EditorFixApply = async (violation, replacement, mode) => {
      const wording = replacement.trim();
      if (!wording) return { ok: false, reason: "unlocated" };

      if (mode === "append") {
        const already = editor
          .getEditorState()
          .read(() => normalize($getRoot().getTextContent()).includes(normalize(wording)));
        if (already) return { ok: false, reason: "already-present" };
        editor.update(
          () => {
            const paragraph = $createParagraphNode();
            paragraph.append($createTextNode(wording));
            $getRoot().append(paragraph);
          },
          { discrete: true }
        );
        return { ok: true, text: readText(editor) };
      }

      const plan = editor.getEditorState().read(() => planReplacement(editor, violation, sectionsRef));
      if (!plan) return { ok: false, reason: "unlocated" };

      let applied = false;
      editor.update(
        () => {
          applied = spliceBlock(plan.blockKey, plan.start, plan.end, wording);
        },
        // Synchronous commit: the caller persists immediately afterwards and
        // must post the state the editor now holds, not the one it held before.
        { discrete: true }
      );
      return applied ? { ok: true, text: readText(editor) } : { ok: false, reason: "unlocated" };
    };

    register(apply);
    return () => register(null);
  }, [editor, register, sectionsRef]);

  return null;
}

function readText(editor: LexicalEditor): string {
  return editor.getEditorState().read(() => $getRoot().getTextContent());
}

/** Which block, and which characters of it, the flagged wording occupies.
 *
 * The block comes from the shared anchoring (so a fix lands where the highlight
 * is, never somewhere else), but the offsets are recomputed here against the
 * block's own text nodes rather than reused from the anchor: anchor offsets are
 * in normalized coordinates, and writing at a normalized offset into raw text
 * would eat the wrong characters.
 *
 * Must run inside an editorState.read().
 */
function planReplacement(
  editor: LexicalEditor,
  violation: Violation,
  sectionsRef?: SectionsRef
): { blockKey: string; start: number; end: number } | null {
  const span = violation.current_text?.trim();
  if (!span) return null;
  const nodes: NodeText[] = sectionsRef?.current ?? readBlocks(editor, new Map());
  if (nodes.length === 0) return null;

  const result = locate(violation, indexDocument(nodes));
  // Only a word-precise, single-block locate may rewrite the document. A
  // fingerprint match knows the paragraph but not the words; a match spread
  // over several blocks would need them merged. Both are refused so the
  // reviewer edits by hand rather than having the wrong words replaced.
  if (result.status === "unlocated" || result.status === "fingerprint") return null;
  if (result.spans.length !== 1) return null;

  const block = $getNodeByKey(result.spans[0].nodeKey);
  if (!block || !$isElementNode(block)) return null;
  const raw = block.getAllTextNodes().map((t) => t.getTextContent()).join("");
  const at = findSpan(raw, span);
  return at ? { blockKey: block.getKey(), start: at.start, end: at.end } : null;
}

/** Where `span` sits in `raw`, tolerating the whitespace and case that
 * normalization folded away. Null unless there is exactly one match — an
 * ambiguous replacement is a wrong replacement half the time. */
function findSpan(raw: string, span: string): { start: number; end: number } | null {
  const words = span.trim().split(/\s+/).filter(Boolean);
  if (words.length === 0) return null;
  const pattern = new RegExp(words.map(escapeRegExp).join("\\s+"), "gi");
  let found: { start: number; end: number } | null = null;
  for (let m = pattern.exec(raw); m; m = pattern.exec(raw)) {
    if (found) return null;
    found = { start: m.index, end: m.index + m[0].length };
  }
  return found;
}

function escapeRegExp(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** Replace [start, end) of a block's text with `wording`, across however many
 * text nodes the span covers (a flagged phrase can run through a bold word).
 * The replacement takes the formatting of the node the span starts in.
 *
 * Must run inside an editor.update().
 */
function spliceBlock(blockKey: string, start: number, end: number, wording: string): boolean {
  const block = $getNodeByKey(blockKey);
  if (!block || !$isElementNode(block)) return false;
  const texts = block.getAllTextNodes();

  let at = 0;
  let done = false;
  for (const node of texts) {
    const text = node.getTextContent();
    const from = at;
    const to = at + text.length;
    at = to;
    if (to <= start || from >= end) continue;
    const head = from < start ? text.slice(0, start - from) : "";
    const tail = to > end ? text.slice(end - from) : "";
    // The first covered node carries the replacement; the rest lose only the
    // characters the span actually covered.
    node.setTextContent(done ? head + tail : head + wording + tail);
    done = true;
  }
  return done;
}
