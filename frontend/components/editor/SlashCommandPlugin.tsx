"use client";
import * as React from "react";
import { createPortal } from "react-dom";
import { useLexicalComposerContext } from "@lexical/react/LexicalComposerContext";
import {
  LexicalTypeaheadMenuPlugin,
  MenuOption,
  useBasicTypeaheadTriggerMatch,
  type TriggerFn,
} from "@lexical/react/LexicalTypeaheadMenuPlugin";
import {
  $getSelection,
  $isRangeSelection,
  COMMAND_PRIORITY_LOW,
  type ElementNode,
  type LexicalEditor,
} from "lexical";
import { $createHeadingNode, $createQuoteNode } from "@lexical/rich-text";
import { INSERT_ORDERED_LIST_COMMAND, INSERT_UNORDERED_LIST_COMMAND } from "@lexical/list";
import { INSERT_TABLE_COMMAND } from "@lexical/table";
import { $setBlocksType } from "@lexical/selection";
import {
  Heading1,
  Heading2,
  Heading3,
  List,
  ListOrdered,
  Quote,
  Table,
  type LucideIcon,
} from "lucide-react";
import { cn } from "@/lib/utils";

/** Must run inside `editor.update()`. */
function $setBlock(create: () => ElementNode) {
  const selection = $getSelection();
  if (!$isRangeSelection(selection)) return;
  $setBlocksType(selection, create);
}

type BlockSpec = {
  key: string;
  label: string;
  /** Extra words the query may match, so "/ul" and "/bullet" both find the list. */
  aliases: string[];
  Icon: LucideIcon;
  run: (editor: LexicalEditor) => void;
};

/** Only blocks LexicalDocument registers a node for. No Divider: HorizontalRuleNode
 *  is not in that editor's `NODES`, and offering a block the editor cannot build
 *  would throw on insert. Add it here once the node is registered. */
const BLOCKS: BlockSpec[] = [
  {
    key: "h1",
    label: "Heading 1",
    aliases: ["h1", "title"],
    Icon: Heading1,
    run: () => $setBlock(() => $createHeadingNode("h1")),
  },
  {
    key: "h2",
    label: "Heading 2",
    aliases: ["h2", "subtitle"],
    Icon: Heading2,
    run: () => $setBlock(() => $createHeadingNode("h2")),
  },
  {
    key: "h3",
    label: "Heading 3",
    aliases: ["h3"],
    Icon: Heading3,
    run: () => $setBlock(() => $createHeadingNode("h3")),
  },
  {
    key: "bullet",
    label: "Bulleted list",
    aliases: ["ul", "bullet", "unordered"],
    Icon: List,
    run: (editor) => editor.dispatchCommand(INSERT_UNORDERED_LIST_COMMAND, undefined),
  },
  {
    key: "number",
    label: "Numbered list",
    aliases: ["ol", "number", "ordered"],
    Icon: ListOrdered,
    run: (editor) => editor.dispatchCommand(INSERT_ORDERED_LIST_COMMAND, undefined),
  },
  {
    key: "quote",
    label: "Quote",
    aliases: ["quote", "blockquote"],
    Icon: Quote,
    run: () => $setBlock($createQuoteNode),
  },
  {
    key: "table",
    label: "Table (3×3)",
    aliases: ["table", "grid"],
    Icon: Table,
    run: (editor) => editor.dispatchCommand(INSERT_TABLE_COMMAND, { columns: "3", rows: "3" }),
  },
];

function matching(query: string | null): BlockSpec[] {
  const q = (query ?? "").trim().toLowerCase();
  if (!q) return BLOCKS;
  return BLOCKS.filter(
    (b) => b.label.toLowerCase().includes(q) || b.aliases.some((a) => a.startsWith(q))
  );
}

class BlockOption extends MenuOption {
  constructor(readonly spec: BlockSpec) {
    super(spec.key);
  }
}

/**
 * Slash-command block inserter. Typing `/` at the start of a line or after
 * whitespace opens a caret-anchored menu; typing filters it, arrows move the
 * highlight, Enter inserts and Escape closes.
 *
 * Render inside a `<LexicalComposer>`. Key handlers live in Lexical's own
 * `LexicalMenu`, which only mounts while the menu is open — when it is closed
 * nothing is registered, so Enter and the arrows reach the editor untouched.
 */
export function SlashCommandPlugin(): React.ReactElement | null {
  const [editor] = useLexicalComposerContext();
  const [query, setQuery] = React.useState<string | null>(null);

  const options = React.useMemo(
    () => matching(query).map((spec) => new BlockOption(spec)),
    [query]
  );

  const baseTrigger = useBasicTypeaheadTriggerMatch("/", { minLength: 0 });
  // A query that matches nothing must close the menu outright rather than show
  // an empty one: LexicalMenu swallows the arrow keys whenever it is mounted,
  // so "open with zero options" would eat keystrokes the editor needs.
  const triggerFn = React.useCallback<TriggerFn>(
    (text, ed) => {
      const match = baseTrigger(text, ed);
      return match && matching(match.matchingString).length > 0 ? match : null;
    },
    [baseTrigger]
  );

  return (
    <LexicalTypeaheadMenuPlugin<BlockOption>
      options={options}
      triggerFn={triggerFn}
      onQueryChange={setQuery}
      commandPriority={COMMAND_PRIORITY_LOW}
      onSelectOption={(option, nodeContainingQuery, closeMenu) => {
        editor.update(() => {
          // Drop the typed "/query" before inserting, or it survives as text
          // inside the new block.
          nodeContainingQuery?.remove();
          option.spec.run(editor);
          closeMenu();
        });
      }}
      menuRenderFn={(anchorRef, { selectedIndex, selectOptionAndCleanUp, setHighlightedIndex }) => {
        const anchor = anchorRef.current;
        if (!anchor || options.length === 0) return null;
        // The anchor is Lexical's own absolutely-positioned div on document.body,
        // so the menu clears the editor's overflow-y-auto container. Lexical marks
        // it role="listbox"; ours is the real listbox, so demote the wrapper.
        anchor.setAttribute("role", "presentation");
        return createPortal(
          <ul
            role="listbox"
            aria-label="Insert block"
            className="relative z-50 w-56 overflow-hidden rounded-md border border-border bg-background py-1 shadow-card"
          >
            {options.map((option, i) => (
              <li
                key={option.key}
                id={`typeahead-item-${i}`}
                role="option"
                aria-selected={selectedIndex === i}
                ref={(el) => option.setRefElement(el)}
                onMouseEnter={() => setHighlightedIndex(i)}
                // Without this the editor loses its selection before the click
                // lands, and the insert applies to nothing.
                onMouseDown={(event) => event.preventDefault()}
                onClick={() => selectOptionAndCleanUp(option)}
                className={cn(
                  "flex cursor-pointer items-center gap-2 px-3 py-1.5 text-sm text-muted-foreground",
                  selectedIndex === i && "bg-primary-50 text-primary"
                )}
              >
                <option.spec.Icon className="h-4 w-4 shrink-0" aria-hidden />
                {option.spec.label}
              </li>
            ))}
          </ul>,
          anchor
        );
      }}
    />
  );
}
