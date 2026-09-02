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
import { FindingDecorationsPlugin, type FindingSpot } from "./FindingDecorationsPlugin";
import { FindingBubbles, BUBBLE_LANE } from "./FindingBubbles";
import { SectionIdPlugin } from "./SectionIdPlugin";
import { EditorApplyPlugin, type EditorFixApply } from "./EditorApplyPlugin";
import type { NodeText } from "./findingAnchor";
import { ImageNode } from "./ImageNode";
import { cn } from "@/lib/utils";
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
  onSettled,
}: {
  submissionId: string;
  onFailure: (failure: SeedFailure) => void;
  /** Called once the import has landed, one way or another — success,
   * "nothing to import", or a broken conversion. Lets the caller tell a real
   * empty document (the reviewer cleared it) from this async gap, where the
   * editor is transiently empty only because nothing has arrived yet. */
  onSettled: () => void;
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
          onSettled();
          onFailure({ status: status ?? "failed", reason: reason ?? null });
          return;
        }
        // Settle before the insert: it synchronously fires the editor's
        // onChange (see OnChangePlugin below), and that emission must already
        // read as trustworthy, not as the pre-arrival placeholder.
        onSettled();
        editor.update(() => {
          const dom = new DOMParser().parseFromString(html, "text/html");
          const nodes = $generateNodesFromDOM(editor, dom);
          $getRoot().clear().select();
          $insertNodes(nodes);
        });
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          onSettled();
          onFailure({ status: "failed", reason: (e as Error).message });
        }
      });
    return () => { cancelled = true; };
  }, [editor, submissionId, onFailure, onSettled]);
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

/** Keeps the editor's editable flag in step with the `readOnly` prop.
 *
 * `initialConfig.editable` is read ONCE, at mount, and ReviewTab does not
 * remount this component when the reviewer opens a historical run — so without
 * this effect a read-only record accepted typing. The initialConfig value is
 * kept as well: it is correct for the first paint, and this effect covers every
 * change after it. */
export function EditableSync({ editable }: { editable: boolean }) {
  const [editor] = useLexicalComposerContext();
  React.useEffect(() => {
    editor.setEditable(editable);
  }, [editor, editable]);
  return null;
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
  bubbles = false,
  chromeless = false,
  registerApply,
}: {
  initialState?: Record<string, unknown> | null;
  /** Seed source is fetched from this submission when there is no saved state. */
  submissionId: string;
  readOnly?: boolean;
  /** Whether page images exist, i.e. whether View is a way out when the import
   * fails. Only read for that message. */
  pagesRendered?: boolean;
  /** Show each located finding as a card in the sheet's right margin. Split
   * and Edit both turn this on: Split hides both rails to give the two
   * documents the width, Edit collapses the findings rail by default, and in
   * either case the findings have to go somewhere the reviewer can still see
   * them beside the text they describe. */
  bubbles?: boolean;
  /** The pane already frames this editor (Split's card), so the sheet drops its
   * canvas, border and shadow — two nested sheets is chrome around chrome, in
   * the mode with the least width to spare. Kept separate from `bubbles`: Edit
   * shows cards on a full sheet. */
  chromeless?: boolean;
  /** Offers this editor as the surface Apply-fix writes through. Without it a
   * fix would splice the plain-text copy while the editor's state and HTML —
   * what the export renders — kept the original wording. */
  registerApply?: (apply: EditorFixApply | null) => void;
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
  // Where each located finding sits vertically, in the sheet's coordinates.
  // Measured by the decorations plugin, which already walks every finding to
  // draw it — measuring a second time would be a second source of truth.
  const [spots, setSpots] = React.useState<FindingSpot[]>([]);
  // The finding under the pointer, either on the text or on its card. Only
  // tracked while the cards are shown — otherwise it is a re-render per mouse
  // move for nothing to look at.
  const [hovered, setHovered] = React.useState<string | null>(null);
  // The live blocks and their content-derived ids, maintained by
  // SectionIdPlugin and read by everything that has to find a finding in the
  // document. A ref, not state: it changes on every keystroke and nothing
  // renders from it.
  const sectionsRef = React.useRef<NodeText[] | null>(null);
  // Whether THIS mount's editor has settled on its real content. `initialState`
  // means it already has — the composer applies it synchronously before any
  // listener can fire. With no saved state, SeedFromImport is what settles it
  // (onSettled below); until then, an onChange emission is the placeholder
  // empty document every fresh editor starts from, not a reviewer's edit, and
  // must not reach the parent — a remount (switching View/Split/Edit) is
  // exactly this provider-outlives-the-editor case, and the parent's saved
  // copy is real content the placeholder must never be allowed to overwrite.
  const seededRef = React.useRef(!!initialState);
  const handleSettled = React.useCallback(() => {
    seededRef.current = true;
  }, []);
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
      <EditableSync editable={!readOnly} />
      {/* Toolbar sits outside the scroll container so it stays put while the
          document scrolls. Hidden when read-only: a historical run is a record,
          not a draft, and offering formatting buttons that do nothing is worse
          than offering none. */}
      {!readOnly && <EditorToolbar submissionId={submissionId} />}
      {/* A page, not a text box. The app canvas is grey (globals.css sets
          --surface on the body) and an editor that inherits it reads as a flat
          panel; every document editor floats a white sheet on that canvas
          instead, which is also what View and the rendered pages already show.
          The sheet is the positioned ancestor: the finding overlay layer and
          the margin bubbles are absolutely positioned against it, so they
          scroll with the text and share one coordinate origin. */}
      {/* Split already frames this pane in a card of its own
          (SplitOriginalView), so the sheet drops its canvas, border and shadow
          there — two nested sheets is chrome around chrome, in the mode with
          the least width to spare. */}
      <div
        className={cn(
          "min-h-0 flex-1 overflow-y-auto",
          chromeless ? "px-5 py-6" : "bg-surface px-6 py-5 pb-16"
        )}
        // The cards hang off the sheet's right edge, so the scroll container
        // reserves their lane rather than letting them fall off the pane. Done
        // as padding on the container, so the centred sheet inside it ends up
        // with the sheet-and-cards PAIR centred.
        style={bubbles ? { paddingRight: BUBBLE_LANE + (chromeless ? 20 : 24) } : undefined}
      >
        <div
          className={cn(
            "relative",
            chromeless
              ? "max-w-xl"
              : "mx-auto max-w-[820px] rounded-md border border-border bg-background px-[62px] py-[52px] shadow-sheet"
          )}
        >
          {seedFailure && (
            <ImportFailureNotice failure={seedFailure} pagesRendered={pagesRendered} />
          )}
          {/* The document reads in its own typeface at its own measure. Sizes
              are the design's: body 15.5/1.78, headings stepped off it, and
              every block spaced so a paragraph is a paragraph rather than a
              row in a list. Set here rather than on the ContentEditable so the
              rules also reach nodes Lexical renders itself (h1-h3, li, table). */}
          <RichTextPlugin
            contentEditable={
              <ContentEditable
                className={cn(
                  "font-serif text-[15.5px] leading-[1.78] text-foreground outline-none",
                  "[&_p]:mb-[18px] [&_p:last-child]:mb-0",
                  "[&_h1]:mb-1.5 [&_h1]:text-[29px] [&_h1]:font-semibold [&_h1]:leading-[1.25] [&_h1]:tracking-[-0.01em]",
                  "[&_h2]:mb-3 [&_h2]:mt-6 [&_h2]:text-[19px] [&_h2]:font-semibold",
                  "[&_h3]:mb-2 [&_h3]:mt-5 [&_h3]:text-[16px] [&_h3]:font-semibold",
                  "[&_ul]:mb-[18px] [&_ul]:list-disc [&_ul]:pl-6",
                  "[&_ol]:mb-[18px] [&_ol]:list-decimal [&_ol]:pl-6",
                  "[&_blockquote]:mb-[18px] [&_blockquote]:border-l-2 [&_blockquote]:border-border [&_blockquote]:pl-4 [&_blockquote]:text-muted-foreground",
                  "[&_table]:mb-[18px] [&_table]:w-full [&_table]:border-collapse",
                  "[&_td]:border [&_td]:border-border [&_td]:p-2 [&_td]:align-top",
                  "[&_th]:border [&_th]:border-border [&_th]:bg-subtle [&_th]:p-2 [&_th]:text-left"
                )}
              />
            }
            placeholder={null}
            ErrorBoundary={LexicalErrorBoundary}
          />
          {/* Inside the sheet, so it shares the origin the plugin measures
              against and scrolls with the text it annotates. */}
          {violations && violations.length > 0 && (
            <FindingDecorationsPlugin
              violations={violations}
              selectedViolationId={selectedViolationId ?? null}
              hoveredViolationId={bubbles ? hovered : null}
              sectionsRef={sectionsRef}
              onSelect={onSelectViolation}
              onHover={bubbles ? setHovered : undefined}
              onResolved={onUnlocatedFindings}
              onPlaced={bubbles ? setSpots : undefined}
            />
          )}
          {bubbles && violations && (
            <FindingBubbles
              violations={violations}
              spots={spots}
              selectedViolationId={selectedViolationId ?? null}
              hoveredViolationId={hovered}
              onSelect={onSelectViolation}
              onHover={setHovered}
            />
          )}
        </div>
        <SectionIdPlugin sectionsRef={sectionsRef} />
        {registerApply && !readOnly && (
          <EditorApplyPlugin register={registerApply} sectionsRef={sectionsRef} />
        )}
        <HistoryPlugin />
        <ListPlugin />
        <LinkPlugin />
        <TablePlugin />
        {!readOnly && <SlashCommandPlugin />}
        {onChange && (
          <OnChangePlugin
            ignoreSelectionChange
            onChange={(editorState, editor) => {
              // Before settling, this mount's content is the placeholder the
              // editor starts every fresh instance with, not the reviewer's
              // doing — reporting it would read as a deliberate clear to
              // whoever consumes `onChange` (see `seededRef` above).
              if (!seededRef.current) return;
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
          <SeedFromImport
            submissionId={submissionId}
            onFailure={setSeedFailure}
            onSettled={handleSettled}
          />
        )}
      </div>
    </LexicalComposer>
  );
}
