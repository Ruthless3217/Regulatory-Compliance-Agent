// @vitest-environment jsdom
// frontend/components/editor/__tests__/lexicalAdapter.dom.test.tsx
/** Runs the document-adapter contract against Lexical, the implementation that
 * currently ships. This file's job is to freeze known-good behaviour BEFORE the
 * TipTap port starts. When the port lands, tiptapAdapter.dom.test.tsx calls
 * runAdapterContract with the same fixtures and must agree. */
import * as React from "react";
import { act, render } from "@testing-library/react";
import { $getRoot, $insertNodes } from "lexical";
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
};

runAdapterContract(lexicalAdapter);
