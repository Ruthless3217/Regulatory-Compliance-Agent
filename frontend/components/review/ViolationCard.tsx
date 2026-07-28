"use client";
import * as React from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { SeverityBadge, Badge } from "@/components/ui/badge";
import { categoryLabel, severityClass, truthyAutoFix, normalizeSeverity } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ActionTags } from "@/components/violation/ActionTags";
import { PrecedentNote } from "@/components/violation/PrecedentNote";
import { submitViolationFeedback } from "@/lib/api";
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

  // Reviewer verdict (adaptive rule weights). Re-clicking flips the verdict;
  // the backend reverts the previous pseudo-count so nothing double-counts.
  const [verdict, setVerdict] = React.useState<"accept" | "reject" | null>(null);
  const [verdictBusy, setVerdictBusy] = React.useState(false);

  const sendVerdict = async (v: "accept" | "reject") => {
    if (verdictBusy || verdict === v) return;
    setVerdictBusy(true);
    try {
      const res = await submitViolationFeedback(violation.id, { verdict: v });
      setVerdict(v);
      if (res.weight_updated && res.reliability != null) {
        toast.success(
          `Verdict recorded — rule reliability now ${Math.round(res.reliability * 100)}%`
        );
      } else {
        toast.success("Verdict recorded");
      }
    } catch {
      toast.error("Could not record verdict");
    } finally {
      setVerdictBusy(false);
    }
  };

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
        "group relative border border-border border-l-2 bg-background p-4 cursor-pointer transition-colors",
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
          {violation.suppressed && (
            <Badge tone="medium" title={violation.suppressed_reason ?? undefined}>
              needs review
            </Badge>
          )}
          {violation.violation_metadata?.verdict_provenance && (
            <Badge
              tone="neutral"
              title={
                violation.violation_metadata.verdict_provenance === "hybrid"
                  ? "Obligation detected by the LLM backstop; wording judged deterministically"
                  : "Obligation and wording both judged deterministically"
              }
            >
              {violation.violation_metadata.verdict_provenance === "hybrid"
                ? "hybrid (LLM trigger)"
                : "deterministic"}
            </Badge>
          )}
        </div>
        <div className="font-mono text-xs text-muted-foreground">
          {typeof violation.chunk_index === "number" && (
            <span className="mr-2">chunk {violation.chunk_index}</span>
          )}
          #{String(index + 1).padStart(2, "0")}
        </div>
      </div>

      <p className="text-sm leading-snug">{violation.description}</p>

      <ActionTags violation={violation} className="mt-2 flex flex-wrap items-center gap-1.5" />

      {violation.current_text && (
        <div className="mt-3 rounded-sm border border-border bg-background p-2 text-xs">
          <div className="micro-label mb-1">Evidence</div>
          <p className="line-clamp-3">
            “
            <mark data-severity={normalizeSeverity(violation.severity)}>
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

      {/* Disclosure verdict evidence: what the matcher actually looked at and
          the minimum valid change that would flip the verdict. */}
      {violation.violation_metadata?.grounding === "disclosure" &&
        violation.violation_metadata.match_method && (
          <div className="mt-3 rounded-sm border border-border bg-background p-2 text-xs">
            <div className="micro-label mb-1">Match evidence</div>
            <p className="text-muted-foreground">
              {violation.violation_metadata.match_method}
              {typeof violation.violation_metadata.normalized_similarity === "number" &&
                ` · normalised ${violation.violation_metadata.normalized_similarity.toFixed(2)}`}
              {typeof violation.violation_metadata.raw_similarity === "number" &&
                ` · raw ${violation.violation_metadata.raw_similarity.toFixed(2)}`}
              {violation.violation_metadata.match_reason &&
                ` · ${violation.violation_metadata.match_reason.replace(/_/g, " ")}`}
            </p>
            {violation.violation_metadata.evidence_span && (
              <p className="mt-1 line-clamp-2">
                closest span: “{violation.violation_metadata.evidence_span}”
              </p>
            )}
            {violation.violation_metadata.counterfactual && (
              <p className="mt-1 italic text-muted-foreground">
                {violation.violation_metadata.counterfactual}
              </p>
            )}
          </div>
        )}

      {/* Provenance: the precedent's reviewer comment, or the novel-finding
          regulatory basis. The substance behind the flag. */}
      <div className="mt-3 [&:empty]:hidden">
        <PrecedentNote violation={violation} />
      </div>

      {violation.suggested_fix && (
        <div className="mt-3 rounded-sm border border-success/40 bg-success/5 p-2">
          <div className="micro-label mb-1 text-success">Suggested fix</div>
          <p className="text-xs">{violation.suggested_fix}</p>
        </div>
      )}

      <div className="mt-3 flex items-center justify-between gap-2">
        <div className="flex items-center gap-1.5" title="Your verdict tunes this rule's weight">
          <span className="micro-label text-muted-foreground">Verdict</span>
          <Button
            variant={verdict === "accept" ? "default" : "ghost"}
            size="sm"
            disabled={verdictBusy}
            aria-pressed={verdict === "accept"}
            onClick={(e) => { e.stopPropagation(); sendVerdict("accept"); }}
          >
            Correct
          </Button>
          <Button
            variant={verdict === "reject" ? "default" : "ghost"}
            size="sm"
            disabled={verdictBusy}
            aria-pressed={verdict === "reject"}
            onClick={(e) => { e.stopPropagation(); sendVerdict("reject"); }}
          >
            Not a violation
          </Button>
        </div>
        <div className="flex items-center gap-2">
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
    </div>
  );
});
