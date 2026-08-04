"use client";
import * as React from "react";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";
import {
  $createParagraphNode,
  $getSelection,
  $isRangeSelection,
  $isRootOrShadowRoot,
  CAN_REDO_COMMAND,
  CAN_UNDO_COMMAND,
  COMMAND_PRIORITY_CRITICAL,
  FORMAT_TEXT_COMMAND,
  REDO_COMMAND,
  UNDO_COMMAND,
} from "lexical";
import {
  $createHeadingNode,
  $createQuoteNode,
  $isHeadingNode,
  $isQuoteNode,
} from "@lexical/rich-text";
import {
  $isListNode,
  INSERT_ORDERED_LIST_COMMAND,
  INSERT_UNORDERED_LIST_COMMAND,
  REMOVE_LIST_COMMAND,
} from "@lexical/list";
import { $setBlocksType } from "@lexical/selection";
import { $findMatchingParent, mergeRegister } from "@lexical/utils";
import {
  Bold,
  Heading1,
  Heading2,
  Heading3,
  Italic,
  List,
  ListOrdered,
  Pilcrow,
  Quote,
  Redo2,
  Underline,
  Undo2,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";

/** Only the blocks the registered nodes can actually produce. Anything else the
 *  selection lands in reports as "paragraph", so no button lights up for a node
 *  this toolbar cannot create. */
type Block = "paragraph" | "h1" | "h2" | "h3" | "bullet" | "number" | "quote";

type Active = { block: Block; bold: boolean; italic: boolean; underline: boolean };

const INITIAL: Active = { block: "paragraph", bold: false, italic: false, underline: false };

/** Must run inside `editorState.read()` / `editor.update()`. */
function $readActive(): Active | null {
  const selection = $getSelection();
  if (!$isRangeSelection(selection)) return null;

  // The nearest block under the root *or a shadow root*, so a caret inside a
  // table cell reports the heading in that cell, not the whole table.
  const anchor = selection.anchor.getNode();
  const element =
    anchor.getKey() === "root"
      ? anchor
      : ($findMatchingParent(anchor, (node) => {
          const parent = node.getParent();
          return parent !== null && $isRootOrShadowRoot(parent);
        }) ?? anchor.getTopLevelElementOrThrow());

  let block: Block = "paragraph";
  if ($isListNode(element)) {
    block = element.getListType() === "number" ? "number" : "bullet";
  } else if ($isHeadingNode(element)) {
    const tag = element.getTag();
    if (tag === "h1" || tag === "h2" || tag === "h3") block = tag;
  } else if ($isQuoteNode(element)) {
    block = "quote";
  }

  return {
    block,
    bold: selection.hasFormat("bold"),
    italic: selection.hasFormat("italic"),
    underline: selection.hasFormat("underline"),
  };
}

function ToolbarButton({
  label,
  active,
  disabled,
  onClick,
  children,
}: {
  label: string;
  /** Omit for buttons that are actions, not toggles — undo/redo have no pressed state. */
  active?: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button
      type="button"
      size="icon"
      variant="ghost"
      aria-label={label}
      aria-pressed={active}
      title={label}
      disabled={disabled}
      // Without this the button takes focus and the editor drops its selection
      // before the command runs, so the format applies to nothing.
      onMouseDown={(event) => event.preventDefault()}
      onClick={onClick}
      className={cn("h-7 w-7 text-body hover:bg-surface", active && "bg-primary-50 text-primary")}
    >
      {children}
    </Button>
  );
}

export function EditorToolbar(): React.ReactElement {
  const [editor] = useLexicalComposerContext();
  const [active, setActive] = React.useState<Active>(INITIAL);
  const [canUndo, setCanUndo] = React.useState(false);
  const [canRedo, setCanRedo] = React.useState(false);

  React.useEffect(
    () =>
      mergeRegister(
        // Update listeners fire for selection-only changes as well, so this one
        // registration covers both "caret moved" and "content changed".
        editor.registerUpdateListener(({ editorState }) => {
          const next = editorState.read($readActive);
          if (next) setActive(next);
        }),
        editor.registerCommand(
          CAN_UNDO_COMMAND,
          (payload) => {
            setCanUndo(payload);
            return false;
          },
          COMMAND_PRIORITY_CRITICAL
        ),
        editor.registerCommand(
          CAN_REDO_COMMAND,
          (payload) => {
            setCanRedo(payload);
            return false;
          },
          COMMAND_PRIORITY_CRITICAL
        )
      ),
    [editor]
  );

  const setBlock = (next: Block) => {
    if (next === "bullet" || next === "number") {
      if (active.block === next) {
        editor.dispatchCommand(REMOVE_LIST_COMMAND, undefined);
        return;
      }
      editor.dispatchCommand(
        next === "bullet" ? INSERT_UNORDERED_LIST_COMMAND : INSERT_ORDERED_LIST_COMMAND,
        undefined
      );
      return;
    }
    // Clicking the block you are already in returns to a paragraph, so every
    // block button is a toggle rather than a one-way trip.
    const target = active.block === next ? "paragraph" : next;
    editor.update(() => {
      const selection = $getSelection();
      if (!$isRangeSelection(selection)) return;
      $setBlocksType(selection, () =>
        target === "paragraph"
          ? $createParagraphNode()
          : target === "quote"
            ? $createQuoteNode()
            : $createHeadingNode(target)
      );
    });
  };

  return (
    <div
      role="toolbar"
      aria-label="Text formatting"
      className="flex h-10 shrink-0 flex-wrap items-center gap-px border-b border-border bg-background px-3"
    >
      <ToolbarButton
        label="Undo"
        disabled={!canUndo}
        onClick={() => editor.dispatchCommand(UNDO_COMMAND, undefined)}
      >
        <Undo2 className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        label="Redo"
        disabled={!canRedo}
        onClick={() => editor.dispatchCommand(REDO_COMMAND, undefined)}
      >
        <Redo2 className="h-4 w-4" />
      </ToolbarButton>

      <Separator orientation="vertical" className="mx-1 h-5" />

      <ToolbarButton
        label="Bold"
        active={active.bold}
        onClick={() => editor.dispatchCommand(FORMAT_TEXT_COMMAND, "bold")}
      >
        <Bold className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        label="Italic"
        active={active.italic}
        onClick={() => editor.dispatchCommand(FORMAT_TEXT_COMMAND, "italic")}
      >
        <Italic className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        label="Underline"
        active={active.underline}
        onClick={() => editor.dispatchCommand(FORMAT_TEXT_COMMAND, "underline")}
      >
        <Underline className="h-4 w-4" />
      </ToolbarButton>

      <Separator orientation="vertical" className="mx-1 h-5" />

      <ToolbarButton
        label="Paragraph"
        active={active.block === "paragraph"}
        onClick={() => setBlock("paragraph")}
      >
        <Pilcrow className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton label="Heading 1" active={active.block === "h1"} onClick={() => setBlock("h1")}>
        <Heading1 className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton label="Heading 2" active={active.block === "h2"} onClick={() => setBlock("h2")}>
        <Heading2 className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton label="Heading 3" active={active.block === "h3"} onClick={() => setBlock("h3")}>
        <Heading3 className="h-4 w-4" />
      </ToolbarButton>

      <Separator orientation="vertical" className="mx-1 h-5" />

      <ToolbarButton
        label="Bulleted list"
        active={active.block === "bullet"}
        onClick={() => setBlock("bullet")}
      >
        <List className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        label="Numbered list"
        active={active.block === "number"}
        onClick={() => setBlock("number")}
      >
        <ListOrdered className="h-4 w-4" />
      </ToolbarButton>
      <ToolbarButton
        label="Blockquote"
        active={active.block === "quote"}
        onClick={() => setBlock("quote")}
      >
        <Quote className="h-4 w-4" />
      </ToolbarButton>

      <Separator orientation="vertical" className="mx-1 h-[18px]" />

      {/* The slash menu is the only affordance here with no button of its own
          (SlashCommandPlugin), so the toolbar is where it gets discovered. */}
      <span className="px-1 font-mono text-[11px] text-faint">/ for commands</span>
    </div>
  );
}
