"use client";
import * as React from "react";
import { cn } from "@/lib/utils";
import type { ChangeKind, ChangeType } from "@/lib/types";

export interface SemanticMeta {
  label: string;
  tooltip: string;
  badgeClass: string;
  dotClass: string;
}

export function getSemanticMeta(
  changeType?: ChangeType | string,
  kind?: ChangeKind
): SemanticMeta {
  switch (changeType) {
    case "numeric_only":
      return {
        label: "Numeric",
        tooltip: "Numeric change",
        badgeClass:
          "border-sky-500/30 text-sky-700 bg-sky-50/80 dark:border-sky-400/30 dark:text-sky-300 dark:bg-sky-950/40",
        dotClass: "bg-sky-500",
      };
    case "identifier_only":
      return {
        label: "Identifier",
        tooltip: "Identifier change",
        badgeClass:
          "border-indigo-500/30 text-indigo-700 bg-indigo-50/80 dark:border-indigo-400/30 dark:text-indigo-300 dark:bg-indigo-950/40",
        dotClass: "bg-indigo-500",
      };
    case "replacement":
      return {
        label: "Replacement",
        tooltip: "Text replacement",
        badgeClass:
          "border-amber-500/30 text-amber-800 bg-amber-50/80 dark:border-amber-400/30 dark:text-amber-300 dark:bg-amber-950/40",
        dotClass: "bg-amber-500",
      };
    case "insertion":
      return {
        label: "Insertion",
        tooltip: "Inserted text",
        badgeClass:
          "border-success/30 text-success bg-emerald-50/80 dark:bg-emerald-950/40",
        dotClass: "bg-success",
      };
    case "deletion":
      return {
        label: "Deletion",
        tooltip: "Deleted text",
        badgeClass:
          "border-sev-critical/30 text-sev-critical bg-rose-50/80 dark:bg-rose-950/40",
        dotClass: "bg-sev-critical",
      };
    case "reordered":
      return {
        label: "Reordered",
        tooltip: "Section moved",
        badgeClass:
          "border-violet-500/30 text-violet-700 bg-violet-50/80 dark:border-violet-400/30 dark:text-violet-300 dark:bg-violet-950/40",
        dotClass: "bg-violet-600",
      };
    case "punctuation_only":
      return {
        label: "Formatting",
        tooltip: "Punctuation change",
        badgeClass:
          "border-slate-300 text-slate-600 bg-slate-100/80 dark:border-slate-700 dark:text-slate-300 dark:bg-slate-800/40",
        dotClass: "bg-slate-400",
      };
    case "whitespace_only":
      return {
        label: "Formatting",
        tooltip: "Whitespace change",
        badgeClass:
          "border-slate-300 text-slate-600 bg-slate-100/80 dark:border-slate-700 dark:text-slate-300 dark:bg-slate-800/40",
        dotClass: "bg-slate-400",
      };
    default:
      // Fallback based on ChangeKind if change_type is absent or unknown
      if (kind === "removed") {
        return {
          label: "Removed",
          tooltip: "Deleted text",
          badgeClass:
            "border-sev-critical/30 text-sev-critical bg-rose-50/80 dark:bg-rose-950/40",
          dotClass: "bg-sev-critical",
        };
      }
      if (kind === "added") {
        return {
          label: "Added",
          tooltip: "Inserted text",
          badgeClass:
            "border-success/30 text-success bg-emerald-50/80 dark:bg-emerald-950/40",
          dotClass: "bg-success",
        };
      }
      if (kind === "moved") {
        return {
          label: "Moved",
          tooltip: "Section moved",
          badgeClass:
            "border-violet-500/30 text-violet-700 bg-violet-50/80 dark:border-violet-400/30 dark:text-violet-300 dark:bg-violet-950/40",
          dotClass: "bg-violet-600",
        };
      }
      if (kind === "modified") {
        return {
          label: "Modified",
          tooltip: "Text replacement",
          badgeClass:
            "border-primary/30 text-primary bg-primary-50/80 dark:bg-primary-950/40",
          dotClass: "bg-primary",
        };
      }
      return {
        label: "Changed",
        tooltip: "Document change",
        badgeClass: "border-border text-muted-foreground bg-muted/40",
        dotClass: "bg-muted-foreground",
      };
  }
}

export function SemanticBadge({
  changeType,
  kind,
  className,
  showDot = true,
}: {
  changeType?: ChangeType | string;
  kind?: ChangeKind;
  className?: string;
  showDot?: boolean;
}) {
  const meta = getSemanticMeta(changeType, kind);

  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-[3px] border px-1.5 py-0.5 text-[10px] font-medium tracking-tight",
        meta.badgeClass,
        className
      )}
      title={meta.tooltip}
      aria-label={meta.tooltip}
    >
      {showDot && (
        <span
          className={cn("h-1.5 w-1.5 shrink-0 rounded-full", meta.dotClass)}
          aria-hidden="true"
        />
      )}
      <span>{meta.label}</span>
    </span>
  );
}
