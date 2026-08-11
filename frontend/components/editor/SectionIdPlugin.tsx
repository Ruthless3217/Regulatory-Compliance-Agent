"use client";
import * as React from "react";
import { $getRoot, type LexicalEditor } from "lexical";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";

import { nextSectionMap, type SectionEntry } from "./sectionMap";
import type { NodeText } from "./findingAnchor";

/** Keeps a content-derived id on every top-level block of the live document.
 *
 * The ids are what a finding's `anchor_node_key` names, so this is the bridge
 * between a backend that analysed the document hours ago and the Lexical node
 * keys that only exist in this tab. Writes into a ref rather than state: it
 * updates on every keystroke and nothing renders from it — the decorations pass
 * reads it when it next measures, which is the only consumer that needs it.
 *
 * The blocks it walks are the same ones the decorations pass needs, so it
 * publishes them too and that pass does not walk the document a second time.
 */
export type SectionsRef = React.RefObject<NodeText[] | null>;

export function SectionIdPlugin({ sectionsRef }: { sectionsRef: SectionsRef }) {
  const [editor] = useLexicalComposerContext();
  // Previous pass's ids AND texts — the text is what tells a split (one block
  // became two) from a paragraph the reviewer just typed.
  const entriesRef = React.useRef<Map<string, SectionEntry>>(new Map());

  React.useEffect(() => {
    const refresh = (dirty?: ReadonlySet<string> | ReadonlyMap<string, unknown>) => {
      const blocks = readBlocks(editor, entriesRef.current, dirty);
      const entries = nextSectionMap(blocks, entriesRef.current);
      entriesRef.current = entries;
      sectionsRef.current = blocks.map((b) => ({
        key: b.key,
        text: b.text,
        id: entries.get(b.key)?.id ?? null,
      }));
    };

    // registerUpdateListener only fires on the NEXT update, and the first
    // decorate pass happens before any edit.
    refresh();
    return editor.registerUpdateListener(({ dirtyElements }) => refresh(dirtyElements));
  }, [editor, sectionsRef]);

  return null;
}

/** The document's top-level blocks, reusing the text of every block this update
 * did not touch.
 *
 * getTextContent() rebuilds a string per block, so re-reading all of them on
 * every keystroke is the one cost worth avoiding here: typing dirties one
 * paragraph, and only that one is re-read. */
export function readBlocks(
  editor: LexicalEditor,
  cache: ReadonlyMap<string, SectionEntry>,
  dirty?: ReadonlySet<string> | ReadonlyMap<string, unknown>
): Array<{ key: string; text: string }> {
  return editor.getEditorState().read(() =>
    $getRoot()
      .getChildren()
      .map((child) => {
        const key = child.getKey();
        const cached = cache.get(key);
        const clean = cached && dirty && !dirty.has(key);
        return { key, text: clean ? cached.text : child.getTextContent() };
      })
      // A blank line is not a block a finding can be anchored to, and hashing
      // every empty paragraph in a document would mint ids nothing can use.
      .filter((b) => b.text.trim().length > 0)
  );
}
