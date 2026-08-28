// @vitest-environment jsdom
// frontend/components/editor/__tests__/lexicalAdapter.dom.test.tsx
/** Runs the document-adapter contract against Lexical, the implementation that
 * currently ships. This file's job is to freeze known-good behaviour BEFORE the
 * TipTap port starts. When the port lands, tiptapAdapter.dom.test.tsx calls
 * runAdapterContract with the same fixtures and must agree. */
import * as React from "react";
import { act, render } from "@testing-library/react";
import { $getRoot, $insertNodes, $isElementNode, $isTextNode } from "lexical";
import { $generateNodesFromDOM } from "@lexical/html";
import { HeadingNode, QuoteNode } from "@lexical/rich-text";
import { ListItemNode, ListNode } from "@lexical/list";
import { TableCellNode, TableNode, TableRowNode } from "@lexical/table";
import { AutoLinkNode, LinkNode } from "@lexical/link";
import { LexicalComposer } from "@lexical/react/LexicalComposer";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";

import { SectionIdPlugin } from "../SectionIdPlugin";
import { ImageNode } from "../ImageNode";
import type { NodeText } from "../findingAnchor";
import { runAdapterContract, type DocumentAdapter } from "./adapterContract";

// The same list LexicalDocument registers. A missing node type here would make
// the contract pass against an editor the app does not run.
const NODES = [
  HeadingNode, QuoteNode, ListNode, ListItemNode,
  TableNode, TableRowNode, TableCellNode, AutoLinkNode, LinkNode,
  ImageNode,
];

function Seed({ html }: { html: string }) {
  const [editor] = useLexicalComposerContext();
  React.useEffect(() => {
    editor.update(() => {
      const dom = new DOMParser().parseFromString(html, "text/html");
      const nodes = $generateNodesFromDOM(editor, dom);
      $getRoot().clear().select();
      $insertNodes(nodes);
    });
  }, [editor, html]);
  return null;
}

// Hands the mounted editor instance out to the test so blocksAcrossEdit can
// drive a second `editor.update()` against the SAME editor Seed populated —
// mounting a second editor for the `after` read is exactly what this test
// exists to rule out.
function EditorHandle({ editorRef }: { editorRef: { current: ReturnType<typeof useLexicalComposerContext>[0] | null } }) {
  const [editor] = useLexicalComposerContext();
  editorRef.current = editor;
  return null;
}

const lexicalAdapter: DocumentAdapter = {
  name: "lexical",
  async blocksFromHtml(html: string): Promise<NodeText[]> {
    // A plain object rather than React.createRef(): SectionsRef is
    // `React.RefObject<NodeText[] | null>`, which is structurally just
    // `{ current: NodeText[] | null }`, and createRef outside a component adds
    // nothing. The adapter reads `.current` after the render settles.
    const sectionsRef: { current: NodeText[] | null } = { current: null };

    await act(async () => {
      render(
        <LexicalComposer
          initialConfig={{
            namespace: "adapter-contract",
            nodes: NODES,
            onError(e: Error) { throw e; },
          }}
        >
          <Seed html={html} />
          <SectionIdPlugin sectionsRef={sectionsRef} />
        </LexicalComposer>
      );
    });

    return sectionsRef.current ?? [];
  },

  async blocksAcrossEdit(
    html: string,
    index: number,
    newText: string
  ): Promise<{ before: NodeText[]; after: NodeText[] }> {
    const sectionsRef: { current: NodeText[] | null } = { current: null };
    const editorRef: { current: ReturnType<typeof useLexicalComposerContext>[0] | null } = { current: null };

    // ONE mount. `EditorHandle` just exposes the editor instance that Seed and
    // SectionIdPlugin already share, so the edit below runs against it too.
    await act(async () => {
      render(
        <LexicalComposer
          initialConfig={{
            namespace: "adapter-contract",
            nodes: NODES,
            onError(e: Error) { throw e; },
          }}
        >
          <Seed html={html} />
          <EditorHandle editorRef={editorRef} />
          <SectionIdPlugin sectionsRef={sectionsRef} />
        </LexicalComposer>
      );
    });

    // sectionsRef.current is mutated in place by SectionIdPlugin on every
    // subsequent update, so `before` must be a copy taken now — returning the
    // live array would make `before` and `after` the same object and every
    // comparison in the contract cases would pass vacuously.
    const before = [...(sectionsRef.current ?? [])];

    const editor = editorRef.current;
    if (!editor) throw new Error("lexical adapter: editor did not mount");

    await act(async () => {
      editor.update(() => {
        const block = $getRoot().getChildren()[index];
        const first = $isElementNode(block) ? block.getFirstChild() : null;
        if (!$isTextNode(first)) {
          throw new Error(
            `lexical adapter: block ${index}'s first child is not a TextNode`
          );
        }
        first.setTextContent(newText);
      });
    });

    const after = [...(sectionsRef.current ?? [])];

    return { before, after };
  },
};

runAdapterContract(lexicalAdapter);
