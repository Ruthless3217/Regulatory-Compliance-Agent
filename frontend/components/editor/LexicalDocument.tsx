"use client";
import * as React from "react";
import { $getRoot, $insertNodes, type SerializedEditorState } from "lexical";
import { $generateNodesFromDOM, $generateHtmlFromNodes } from "@lexical/html";
import { HeadingNode, QuoteNode } from "@lexical/rich-text";
import { ListItemNode, ListNode } from "@lexical/list";
import { TableCellNode, TableNode, TableRowNode } from "@lexical/table";
import { AutoLinkNode, LinkNode } from "@lexical/link";
import { LexicalComposer } from "@lexical/react/LexicalComposer";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";
import { RichTextPlugin } from "@lexical/react/LexicalRichTextPlugin";
import { ContentEditable } from "@lexical/react/LexicalContentEditable";
import { LexicalErrorBoundary } from "@lexical/react/LexicalErrorBoundary";
import { HistoryPlugin } from "@lexical/react/LexicalHistoryPlugin";
import { ListPlugin } from "@lexical/react/LexicalListPlugin";
import { LinkPlugin } from "@lexical/react/LexicalLinkPlugin";
import { TablePlugin } from "@lexical/react/LexicalTablePlugin";
import { OnChangePlugin } from "@lexical/react/LexicalOnChangePlugin";

import { EditorToolbar } from "./EditorToolbar";
import { SlashCommandPlugin } from "./SlashCommandPlugin";
import { FindingDecorationsPlugin } from "./FindingDecorationsPlugin";
import type { Violation } from "@/lib/types";

const NODES = [
  HeadingNode, QuoteNode, ListNode, ListItemNode,
  TableNode, TableRowNode, TableCellNode, AutoLinkNode, LinkNode,
];

/** Seeds the editor from imported HTML exactly once. Runs only when there is
 * no saved state — after the first save, lexical_state is authoritative and
 * re-seeding would discard the reviewer's edits. */
function SeedFromHtml({ html }: { html: string }) {
  const [editor] = useLexicalComposerContext();
  const seeded = React.useRef(false);
  React.useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;
    editor.update(() => {
      const dom = new DOMParser().parseFromString(html, "text/html");
      const nodes = $generateNodesFromDOM(editor, dom);
      $getRoot().clear().select();
      $insertNodes(nodes);
    });
  }, [editor, html]);
  return null;
}

export function LexicalDocument({
  initialState,
  initialHtml,
  readOnly = false,
  onChange,
  violations,
  selectedViolationId,
  onSelectViolation,
  onUnlocatedFindings,
}: {
  initialState?: Record<string, unknown> | null;
  initialHtml?: string | null;
  readOnly?: boolean;
  onChange?: (doc: { state: SerializedEditorState; html: string }) => void;
  /** Findings to draw on the document. Decorations only — never editor content,
   * so they cannot reach the exported DOCX. */
  violations?: Violation[];
  selectedViolationId?: string | null;
  onSelectViolation?: (id: string) => void;
  /** Findings whose text could no longer be located after editing. Reported so
   * the UI can say so rather than silently omitting them. */
  onUnlocatedFindings?: (unlocated: Array<{ id: string; reason: string }>) => void;
}) {
  const config = {
    namespace: "compliance-document",
    editable: !readOnly,
    nodes: NODES,
    editorState: initialState ? JSON.stringify(initialState) : undefined,
    onError(error: Error) {
      // Never swallow: a thrown node error silently empties the document.
      throw error;
    },
  };

  return (
    <LexicalComposer initialConfig={config}>
      {/* Toolbar sits outside the scroll container so it stays put while the
          document scrolls. Hidden when read-only: a historical run is a record,
          not a draft, and offering formatting buttons that do nothing is worse
          than offering none. */}
      {!readOnly && <EditorToolbar />}
      <div className="relative min-h-0 flex-1 overflow-y-auto px-8 py-6">
        <RichTextPlugin
          contentEditable={<ContentEditable className="outline-none" />}
          placeholder={null}
          ErrorBoundary={LexicalErrorBoundary}
        />
        <HistoryPlugin />
        <ListPlugin />
        <LinkPlugin />
        <TablePlugin />
        {!readOnly && <SlashCommandPlugin />}
        {violations && violations.length > 0 && (
          <FindingDecorationsPlugin
            violations={violations}
            selectedViolationId={selectedViolationId ?? null}
            onSelect={onSelectViolation}
            onResolved={onUnlocatedFindings}
          />
        )}
        {onChange && (
          <OnChangePlugin
            ignoreSelectionChange
            onChange={(editorState, editor) => {
              // Serialize both views in one pass, from the same state. Doing
              // them separately would let export render a document the editor
              // never displayed.
              editorState.read(() => {
                onChange({
                  state: editorState.toJSON(),
                  html: $generateHtmlFromNodes(editor, null),
                });
              });
            }}
          />
        )}
        {!initialState && initialHtml && <SeedFromHtml html={initialHtml} />}
      </div>
    </LexicalComposer>
  );
}
