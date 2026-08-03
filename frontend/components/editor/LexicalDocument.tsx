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
import { getSubmissionImportHtml } from "@/lib/api";

import { EditorToolbar } from "./EditorToolbar";
import { SlashCommandPlugin } from "./SlashCommandPlugin";
import { FindingDecorationsPlugin } from "./FindingDecorationsPlugin";
import { ImageNode } from "./ImageNode";
import type { Violation } from "@/lib/types";

const NODES = [
  HeadingNode, QuoteNode, ListNode, ListItemNode,
  TableNode, TableRowNode, TableCellNode, AutoLinkNode, LinkNode,
  ImageNode,
];

/** Why the editor holds no imported document.
 *
 * `unavailable` is a property of the document (a scanned PDF, an empty file, a
 * missing upload); `failed` is a conversion that broke. `reason` is the
 * backend's reviewer-facing sentence. */
type SeedFailure = { status: string; reason: string | null };

/** Seeds the editor from imported HTML exactly once. Runs only when there is
 * no saved state — after the first save, lexical_state is authoritative and
 * re-seeding would discard the reviewer's edits.
 *
 * Reports failure rather than swallowing it: an empty editable page is the
 * same picture whether the import worked on a blank document, had nothing to
 * import, or broke — and the reviewer was left to guess which. */
function SeedFromImport({
  submissionId,
  onFailure,
}: {
  submissionId: string;
  onFailure: (failure: SeedFailure) => void;
}) {
  const [editor] = useLexicalComposerContext();
  const seeded = React.useRef(false);
  React.useEffect(() => {
    if (seeded.current) return;
    seeded.current = true;
    let cancelled = false;
    // Fetched, not passed in: converting a long document takes seconds, and
    // doing it inside GET /submissions/{id} made opening one time out.
    getSubmissionImportHtml(submissionId)
      .then((res) => {
        if (cancelled) return;
        // The route also returns status/reason; the shared api helper still
        // types its result as {html} alone (that module is owned elsewhere).
        const { html, status, reason } = res as {
          html: string | null;
          status?: string;
          reason?: string | null;
        };
        if (!html) {
          onFailure({ status: status ?? "failed", reason: reason ?? null });
          return;
        }
        editor.update(() => {
          const dom = new DOMParser().parseFromString(html, "text/html");
          const nodes = $generateNodesFromDOM(editor, dom);
          $getRoot().clear().select();
          $insertNodes(nodes);
        });
      })
      .catch((e: unknown) => {
        if (!cancelled) onFailure({ status: "failed", reason: (e as Error).message });
      });
    return () => { cancelled = true; };
  }, [editor, submissionId, onFailure]);
  return null;
}

/** Shown in place of the document the editor could not seed.
 *
 * States the cause and the ways out, because the reviewer's next move differs:
 * a scanned PDF will never be editable here, while a conversion failure may
 * survive a re-upload. Both leave the original readable in View/Split, which
 * is what they actually need to keep reviewing. */
function ImportFailureNotice({
  failure,
  pagesRendered,
}: {
  failure: SeedFailure;
  pagesRendered?: boolean;
}) {
  const unavailable = failure.status === "unavailable";
  return (
    <div
      role="status"
      className="mb-4 rounded-sm border border-border bg-sev-medium/5 px-3 py-2 text-[13px] leading-relaxed"
    >
      <p className="font-medium">
        {unavailable
          ? "There is nothing to import into the editor."
          : "This document could not be converted for editing."}
      </p>
      <p className="mt-1 text-muted-foreground">
        {failure.reason ?? "The conversion failed and reported no detail."}
      </p>
      <p className="mt-1 text-muted-foreground">
        This page is blank because nothing was imported. The uploaded original is unchanged and
        still readable in {pagesRendered ? "View (rendered pages) or Split" : "Split"}, and the
        findings on the right were graded on the extracted text, so they still apply. Anything you
        type here starts an empty working document — an export would then be built from it instead
        of the original wording.
      </p>
    </div>
  );
}

export function LexicalDocument({
  initialState,
  submissionId,
  readOnly = false,
  onChange,
  violations,
  selectedViolationId,
  onSelectViolation,
  onUnlocatedFindings,
  pagesRendered,
}: {
  initialState?: Record<string, unknown> | null;
  /** Seed source is fetched from this submission when there is no saved state. */
  submissionId: string;
  readOnly?: boolean;
  /** Whether page images exist, i.e. whether View is a way out when the import
   * fails. Only read for that message. */
  pagesRendered?: boolean;
  onChange?: (doc: { state: SerializedEditorState; html: string; text: string }) => void;
  /** Findings to draw on the document. Decorations only — never editor content,
   * so they cannot reach the exported DOCX. */
  violations?: Violation[];
  selectedViolationId?: string | null;
  onSelectViolation?: (id: string) => void;
  /** Findings whose text could no longer be located after editing. Reported so
   * the UI can say so rather than silently omitting them. */
  onUnlocatedFindings?: (unlocated: Array<{ id: string; reason: string }>) => void;
}) {
  const [seedFailure, setSeedFailure] = React.useState<SeedFailure | null>(null);
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
        {seedFailure && (
          <ImportFailureNotice failure={seedFailure} pagesRendered={pagesRendered} />
        )}
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
                  // The plain-text projection the revision stores as `content`.
                  // Findings, search and the fallback exports all read it, so
                  // it must be produced from the same state as the other two.
                  text: $getRoot().getTextContent(),
                });
              });
            }}
          />
        )}
        {!initialState && (
          <SeedFromImport submissionId={submissionId} onFailure={setSeedFailure} />
        )}
      </div>
    </LexicalComposer>
  );
}
