"use client";
import * as React from "react";
import Link from "next/link";
import { ChevronLeft, ChevronRight, Plus, Settings2 } from "lucide-react";
import { cn } from "@/lib/utils";
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { Popover } from "@/components/ui/popover";
import { sideLabel } from "@/lib/format";
import { useViewer, deriveViewerChanges } from "./ViewerContext";
import { ExportPopover } from "./ExportPopover";
import { AdjustComparisonPopover } from "./AdjustComparisonPopover";

/** Small bordered 11px control, mirroring CompareWorkspace's ViewToggle. */
function ToolButton({
  active = false,
  disabled = false,
  onClick,
  title,
  className,
  children,
}: {
  active?: boolean;
  disabled?: boolean;
  onClick?: () => void;
  title?: string;
  className?: string;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      title={title}
      className={cn(
        "inline-flex items-center gap-1 rounded-sm border px-2 py-0.5 text-[11px] transition-colors",
        active
          ? "border-foreground bg-foreground text-background"
          : "border-border text-muted-foreground hover:border-foreground hover:text-foreground",
        disabled && "cursor-not-allowed opacity-40 hover:border-border hover:text-muted-foreground",
        className
      )}
    >
      {children}
    </button>
  );
}

function Divider() {
  return <span className="mx-1 h-4 w-px bg-border" />;
}

export function Toolbar() {
  const {
    comparison,
    hasPixel,
    effectiveMode,
    setViewMode,
    layoutMode,
    setLayoutMode,
    singleSide,
    setSingleSide,
    cursorMode,
    setCursorMode,
    scrollLock,
    setScrollLock,
    showMoves,
    setShowMoves,
    selectedChangeId,
    setSelectedChangeId,
  } = useViewer();

  const changes = React.useMemo(
    () => deriveViewerChanges(comparison, effectiveMode, showMoves),
    [comparison, effectiveMode, showMoves]
  );

  const oldLabel = sideLabel(comparison, "old");
  const newLabel = sideLabel(comparison, "new");

  const step = (dir: 1 | -1) => {
    if (changes.length === 0) return;
    const cur = changes.findIndex((c) => c.id === selectedChangeId);
    const next = cur < 0 ? (dir === 1 ? 0 : changes.length - 1) : (cur + dir + changes.length) % changes.length;
    setSelectedChangeId(changes[next].id);
  };

  const email = () => {
    if (typeof window === "undefined") return;
    const subject = encodeURIComponent(document.title || comparison.title);
    const body = encodeURIComponent(`Comparison: ${comparison.title}\n${window.location.href}`);
    window.location.href = `mailto:?subject=${subject}&body=${body}`;
  };

  return (
    <TooltipProvider>
      <div className="flex flex-wrap items-center gap-1.5 border-b border-border bg-muted/20 px-3 py-1.5">
        {/* Left group */}
        <Link
          href="/compare"
          title="Open the comparisons list"
          className="inline-flex items-center gap-1 rounded-sm border border-border px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-foreground hover:text-foreground"
        >
          Open
        </Link>
        <Link
          href="/compare/new"
          title="Start a new comparison — upload or paste two documents"
          className="inline-flex items-center gap-1 rounded-sm border border-border px-2 py-0.5 text-[11px] text-muted-foreground transition-colors hover:border-foreground hover:text-foreground"
        >
          <Plus className="h-3 w-3" />
          New
        </Link>
        <ToolButton title="Print" onClick={() => window.print()}>
          Print
        </ToolButton>
        <ExportPopover />
        <ToolButton title="Email a link to this comparison" onClick={email}>
          Email
        </ToolButton>
        <AdjustComparisonPopover />

        <Divider />

        {/* Center group */}
        <ToolButton
          active={layoutMode === "side-by-side"}
          onClick={() => setLayoutMode("side-by-side")}
          title="Side-by-side view"
        >
          Side-by-Side
        </ToolButton>
        <ToolButton
          active={layoutMode === "single"}
          onClick={() => setLayoutMode("single")}
          title="Single-document view"
        >
          Single
        </ToolButton>
        {layoutMode === "single" && (
          <>
            <ToolButton
              active={singleSide === "old"}
              onClick={() => setSingleSide("old")}
              title={`Show ${oldLabel}`}
              className="max-w-[180px]"
            >
              <span className="truncate">{oldLabel}</span>
            </ToolButton>
            <ToolButton
              active={singleSide === "new"}
              onClick={() => setSingleSide("new")}
              title={`Show ${newLabel}`}
              className="max-w-[180px]"
            >
              <span className="truncate">{newLabel}</span>
            </ToolButton>
          </>
        )}

        <Divider />

        <ToolButton
          active={cursorMode === "scroll"}
          onClick={() => setCursorMode("scroll")}
          title="Scroll: drag to pan the page"
        >
          Scroll
        </ToolButton>
        <ToolButton
          active={cursorMode === "select"}
          onClick={() => setCursorMode("select")}
          title="Select: native text selection"
        >
          Select
        </ToolButton>
        <ToolButton
          active={scrollLock}
          onClick={() => setScrollLock(!scrollLock)}
          title="Synchronize scrolling of both panes"
        >
          Scroll Lock
        </ToolButton>

        <Divider />

        {/* View toggle — disabled with tooltip when no render is available */}
        {hasPixel ? (
          <>
            <ToolButton
              active={effectiveMode === "pixel"}
              onClick={() => setViewMode("pixel")}
              title="Pixel document view"
            >
              Document
            </ToolButton>
            <ToolButton
              active={effectiveMode === "text"}
              onClick={() => setViewMode("text")}
              title="Text redline view"
            >
              Text
            </ToolButton>
          </>
        ) : (
          <Tooltip>
            <TooltipTrigger asChild>
              <span className="inline-flex">
                <ToolButton disabled>Document</ToolButton>
              </span>
            </TooltipTrigger>
            <TooltipContent>
              {comparison.render_status === "processing"
                ? "Rendering document view…"
                : "Document view needs both sides to be PDFs."}
            </TooltipContent>
          </Tooltip>
        )}

        {/* Changes settings popover */}
        <Popover
          align="start"
          className="w-56 p-3"
          trigger={({ open, toggle }) => (
            <ToolButton active={open} onClick={toggle} title="Changes settings">
              <Settings2 className="h-3 w-3" />
              Settings
            </ToolButton>
          )}
        >
          <div className="space-y-2">
            <div className="micro-label">Moves</div>
            <label className="flex cursor-pointer items-center gap-2 text-[12px]">
              <input
                type="checkbox"
                checked={showMoves}
                onChange={(e) => setShowMoves(e.target.checked)}
              />
              Enable Moves
            </label>
            <p className="text-[11px] leading-snug text-muted-foreground">
              Show relocated content as a single violet &ldquo;moved&rdquo; change instead of a
              separate deletion and insertion.
            </p>
          </div>
        </Popover>

        {/* Right group */}
        <div className="ml-auto flex items-center gap-1.5">
          <ToolButton onClick={() => step(-1)} title="Previous change" disabled={changes.length === 0}>
            <ChevronLeft className="h-3 w-3" />
            Prev
          </ToolButton>
          <ToolButton onClick={() => step(1)} title="Next change" disabled={changes.length === 0}>
            Next
            <ChevronRight className="h-3 w-3" />
          </ToolButton>
        </div>
      </div>
    </TooltipProvider>
  );
}
