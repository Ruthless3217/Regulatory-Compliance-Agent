"use client";
import * as React from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { SeverityBadge, Badge } from "@/components/ui/badge";
import { categoryLabel, severityClass, truthyAutoFix } from "@/lib/format";
import { cn } from "@/lib/utils";
import type { Violation } from "@/lib/types";

interface Props {
  index: number;
  violation: Violation;
  selected: boolean;
  dismissed: boolean;
  onSelect: () => void;
  onDismiss: () => void;
}

export const ViolationCard = React.forwardRef<HTMLDivElement, Props>(function ViolationCard(
  { index, violation, selected, dismissed, onSelect, onDismiss },
  ref
) {
  const sevClass = severityClass(violation.severity).split(" ")[0]; // border-l-*
  const autoFix = truthyAutoFix(violation.auto_fixable);

  const applyFix = async () => {
    if (!violation.suggested_fix) {
      toast.message("No suggested fix on this violation");
      return;
    }
    try {
      await navigator.clipboard.writeText(violation.suggested_fix);
      toast.success("Suggested fix copied to clipboard");
    } catch {
      toast.error("Clipboard write failed");
    }
  };

  return (
    <div
      ref={ref}
      data-violation-id={violation.id}
      data-selected={selected ? "true" : "false"}
      data-pulse={selected ? "true" : "false"}
      onClick={onSelect}
      className={cn(
        "group relative border border-border border-l-2 bg-surface p-4 cursor-pointer transition-colors",
        "hover:bg-muted/40",
        sevClass,
        selected && "bg-primary-50",
        dismissed && "opacity-50"
      )}
    >
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="flex items-center gap-1.5">
          <SeverityBadge severity={violation.severity} />
          <Badge>{categoryLabel(violation.category)}</Badge>
          {autoFix && <Badge tone="primary">auto-fix</Badge>}
          {typeof violation.confidence === "number" && (
            <Badge tone={violation.confidence >= 0.85 ? "success" : violation.confidence >= 0.65 ? "medium" : "critical"}>
              {Math.round(violation.confidence * 100)}%
            </Badge>
          )}
        </div>
        <div className="font-mono text-xs text-muted-foreground">#{String(index + 1).padStart(2, "0")}</div>
      </div>

      <p className="text-sm leading-snug">{violation.description}</p>

      {violation.current_text && (
        <div className="mt-3 rounded-sm border border-border bg-background p-2 text-xs">
          <div className="micro-label mb-1">Evidence</div>
          <p className="line-clamp-3">
            “
            <mark data-severity={violation.severity.toLowerCase()}>
              {violation.current_text}
            </mark>
            ”
          </p>
        </div>
      )}

      {violation.regulator_quote && (
        <div className="mt-3 rounded-sm border border-primary/30 bg-primary-50/50 p-2 text-xs">
          <div className="micro-label mb-1 text-primary">Regulator citation</div>
          <p className="line-clamp-3 italic">“{violation.regulator_quote}”</p>
        </div>
      )}

      {violation.suggested_fix && (
        <div className="mt-3 rounded-sm border border-success/40 bg-success/5 p-2">
          <div className="micro-label mb-1 text-success">Suggested fix</div>
          <p className="text-xs">{violation.suggested_fix}</p>
        </div>
      )}

      <div className="mt-3 flex items-center justify-end gap-2">
        <Button variant="ghost" size="sm" onClick={(e) => { e.stopPropagation(); onDismiss(); }}>
          Dismiss
        </Button>
        <Button
          size="sm"
          variant="outline"
          onClick={(e) => { e.stopPropagation(); applyFix(); }}
          disabled={!violation.suggested_fix}
        >
          Apply fix
        </Button>
      </div>
    </div>
  );
});
