"use client";
import * as React from "react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { SeverityBadge, Badge } from "@/components/ui/badge";
import { Textarea } from "@/components/ui/textarea";
import { categoryLabel, severityClass, truthyAutoFix, normalizeSeverity } from "@/lib/format";
import { cn } from "@/lib/utils";
import { ActionTags } from "@/components/violation/ActionTags";
import { PrecedentNote } from "@/components/violation/PrecedentNote";
import { deleteReviewerViolation, submitReviewerAction } from "@/lib/api";
import { useSubmissionWorkspace } from "@/components/workspace/SubmissionWorkspaceContext";
import type { DismissReason, NotViolationReason, ReviewerActionType, Violation } from "@/lib/types";

interface Props {
  index: number;
  violation: Violation;
  selected: boolean;
  onSelect: () => void;
}

// Legacy rows from the old binary accept/reject shim map onto the taxonomy
// this card now speaks so a verdict recorded before 0023 still renders.
function normalizeVerdict(v?: string | null): ReviewerActionType | null {
  if (v === "accept") return "correct";
  if (v === "reject") return "not_violation";
  if (v === "correct" || v === "not_violation" || v === "dismiss") return v;
  return null;
}

const DISMISS_REASONS: { value: DismissReason; label: string }[] = [
  { value: "duplicate", label: "Duplicate" },
  { value: "vague", label: "Vague" },
  { value: "low-value", label: "Low value" },
  { value: "insufficient-evidence", label: "Insufficient evidence" },
  { value: "needs-human-legal-review", label: "Needs human legal review" },
  { value: "unsupported-format", label: "Unsupported format" },
  { value: "other", label: "Other" },
];

const NOT_VIOLATION_REASONS: { value: NotViolationReason; label: string }[] = [
  { value: "wrong-product", label: "Wrong product" },
  { value: "wrong-section", label: "Wrong section" },
  { value: "wrong-context", label: "Wrong context" },
  { value: "outdated-rule", label: "Outdated rule" },
  { value: "retrieval-mismatch", label: "Retrieval mismatch" },
  { value: "valid-regulatory-exception", label: "Valid regulatory exception" },
  { value: "wrong-severity", label: "Wrong severity" },
  { value: "hallucination", label: "Hallucination" },
  { value: "other", label: "Other" },
];

const SELECT_CLASS =
  "h-8 w-full rounded-md border border-border bg-background px-2.5 py-1 text-sm " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary";

export const ViolationCard = React.forwardRef<HTMLDivElement, Props>(function ViolationCard(
  { index, violation, selected, onSelect },
  ref
) {
  const { documentText, applyEdit, setViolations } = useSubmissionWorkspace();
  const sevClass = severityClass(violation.severity).split(" ")[0]; // border-l-*
  const autoFix = truthyAutoFix(violation.auto_fixable);

  // Reviewer verdict (Correct / Not-a-violation / Dismiss) — the real
  // taxonomy behind POST /compliance/violations/{id}/actions, replacing the
  // old accept/reject shim. Initialized from the persisted verdict so it
  // survives a reload instead of always starting null.
  const [verdict, setVerdict] = React.useState<ReviewerActionType | null>(
    normalizeVerdict(violation.reviewer_verdict)
  );
  const [verdictBusy, setVerdictBusy] = React.useState(false);
  const [activePanel, setActivePanel] = React.useState<ReviewerActionType | null>(null);
  const [dismissReason, setDismissReason] = React.useState<DismissReason | "">("");
  const [notViolationReason, setNotViolationReason] = React.useState<NotViolationReason | "">("");
  const [explanation, setExplanation] = React.useState("");
  const [finalText, setFinalText] = React.useState(violation.suggested_fix ?? "");

  const [applyFixBusy, setApplyFixBusy] = React.useState(false);
  const fixApplied = violation.fix_applied === true;

  // 0031 — reviewer-authored flags are the only deletable findings. A model
  // finding is dismissed/rejected by verdict, never removed.
  const reviewerAuthored = violation.source === "reviewer";
  const [deleteBusy, setDeleteBusy] = React.useState(false);

  const removeFlag = async () => {
    if (deleteBusy) return;
    setDeleteBusy(true);
    try {
      await deleteReviewerViolation(violation.id);
      setViolations((prev) => prev.filter((v) => v.id !== violation.id));
      toast.success("Flag removed");
    } catch (e) {
      toast.error(`Could not remove flag: ${(e as Error).message}`);
      setDeleteBusy(false);
    }
  };

  const togglePanel = (panel: ReviewerActionType) =>
    setActivePanel((cur) => (cur === panel ? null : panel));

  const submitAction = async (
    action: ReviewerActionType,
    payload: { reason?: string; explanation?: string; final_text?: string }
  ) => {
    if (verdictBusy) return;
    setVerdictBusy(true);
    try {
      const res = await submitReviewerAction(violation.id, { action, ...payload });
      setVerdict(action);
      setActivePanel(null);
      setViolations((prev) =>
        prev.map((v) =>
          v.id === violation.id
            ? { ...v, reviewer_verdict: action, review_status: res.review_status, resolved_at: res.resolved_at }
            : v
        )
      );
      if (res.weight_updated && res.reliability != null) {
        toast.success(`Verdict recorded — rule reliability now ${Math.round(res.reliability * 100)}%`);
      } else {
        toast.success("Verdict recorded");
      }
    } catch {
      toast.error("Could not record verdict");
    } finally {
      setVerdictBusy(false);
    }
  };

  const confirmCorrect = () => submitAction("correct", { final_text: finalText.trim() || undefined });

  const confirmNotViolation = () => {
    if (!notViolationReason) {
      toast.error("Pick a reason");
      return;
    }
    if (!explanation.trim()) {
      toast.error("Explanation is required");
      return;
    }
    submitAction("not_violation", { reason: notViolationReason, explanation: explanation.trim() });
  };

  const confirmDismiss = () => {
    if (!dismissReason) {
      toast.error("Pick a reason");
      return;
    }
    submitAction("dismiss", { reason: dismissReason });
  };

  const applyFix = async () => {
    const suggestedFix = violation.suggested_fix;
    if (!suggestedFix || fixApplied || applyFixBusy) return;

    // Reads AND writes the one live working copy in context (same text the
    // DocumentPane renders and its inline span editor writes), so back-to-back
    // fixes compose instead of each splicing a stale base.
    const baseText = documentText;
    const evidence = violation.current_text;

    if (!evidence || !baseText.includes(evidence)) {
      try {
        await navigator.clipboard.writeText(suggestedFix);
        toast.message("Could not auto-locate the flagged text — suggested fix copied to clipboard instead");
      } catch {
        toast.error("Clipboard write failed");
      }
      return;
    }

    setApplyFixBusy(true);
    try {
      // Function form: a suggested fix containing "$&" must stay literal.
      const nextContent = baseText.replace(evidence, () => suggestedFix);
      const ok = await applyEdit(nextContent, "apply_fix", [violation.id]);
      if (!ok) {
        toast.error("Fix applied locally but not saved — use Save in the document toolbar to retry.");
        return;
      }
      const appliedAt = new Date().toISOString();
      setViolations((prev) =>
        prev.map((v) => (v.id === violation.id ? { ...v, fix_applied: true, fix_applied_at: appliedAt } : v))
      );
      toast.success("Fix applied to document");
    } finally {
      setApplyFixBusy(false);
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
        verdict === "dismiss" && "opacity-50"
      )}
    >
      <div className="mb-2 flex items-start justify-between gap-3">
        <div className="flex items-center gap-1.5">
          <SeverityBadge severity={violation.severity} />
          <Badge>{categoryLabel(violation.category)}</Badge>
          {reviewerAuthored && (
            <Badge
              tone="primary"
              title={
                `Added by ${violation.created_by_username ?? "a reviewer"} — not a model finding. ` +
                "Excluded from model-precision metrics."
              }
            >
              reviewer · {violation.created_by_username ?? "added by hand"}
            </Badge>
          )}
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
              tone="default"
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
            variant={verdict === "correct" ? "default" : "ghost"}
            size="sm"
            disabled={verdictBusy}
            aria-pressed={verdict === "correct"}
            onClick={(e) => { e.stopPropagation(); togglePanel("correct"); }}
          >
            Correct
          </Button>
          <Button
            variant={verdict === "not_violation" ? "default" : "ghost"}
            size="sm"
            disabled={verdictBusy}
            aria-pressed={verdict === "not_violation"}
            onClick={(e) => { e.stopPropagation(); togglePanel("not_violation"); }}
          >
            Not a violation
          </Button>
          <Button
            variant={verdict === "dismiss" ? "default" : "ghost"}
            size="sm"
            disabled={verdictBusy}
            aria-pressed={verdict === "dismiss"}
            onClick={(e) => { e.stopPropagation(); togglePanel("dismiss"); }}
          >
            Dismiss
          </Button>
        </div>
        <div className="flex items-center gap-2">
          {fixApplied && <Badge tone="success">Applied</Badge>}
          {reviewerAuthored && (
            <Button
              size="sm"
              variant="ghost"
              title="Remove this reviewer-added flag"
              disabled={deleteBusy}
              onClick={(e) => { e.stopPropagation(); removeFlag(); }}
            >
              {deleteBusy ? "Removing…" : "Remove"}
            </Button>
          )}
          <Button
            size="sm"
            variant="outline"
            onClick={(e) => { e.stopPropagation(); applyFix(); }}
            disabled={!violation.suggested_fix || fixApplied || applyFixBusy}
          >
            {applyFixBusy ? "Applying…" : "Apply fix"}
          </Button>
        </div>
      </div>

      {activePanel === "correct" && (
        <div
          className="mt-2 space-y-2 rounded-sm border border-border bg-muted/20 p-2"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="micro-label text-muted-foreground">Final text</div>
          <Textarea
            value={finalText}
            onChange={(e) => setFinalText(e.target.value)}
            className="min-h-[72px] text-xs"
            placeholder="The text approved as final…"
          />
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setActivePanel(null)}>
              Cancel
            </Button>
            <Button size="sm" disabled={verdictBusy} onClick={confirmCorrect}>
              Confirm correct
            </Button>
          </div>
        </div>
      )}

      {activePanel === "not_violation" && (
        <div
          className="mt-2 space-y-2 rounded-sm border border-border bg-muted/20 p-2"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="micro-label text-muted-foreground">Reason (required)</div>
          <select
            value={notViolationReason}
            onChange={(e) => setNotViolationReason(e.target.value as NotViolationReason)}
            className={SELECT_CLASS}
          >
            <option value="">Select a reason…</option>
            {NOT_VIOLATION_REASONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
          <div className="micro-label text-muted-foreground">Explanation (required)</div>
          <Textarea
            value={explanation}
            onChange={(e) => setExplanation(e.target.value)}
            className="min-h-[64px] text-xs"
            placeholder="Why is this not a violation?"
          />
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setActivePanel(null)}>
              Cancel
            </Button>
            <Button
              size="sm"
              disabled={verdictBusy || !notViolationReason || !explanation.trim()}
              onClick={confirmNotViolation}
            >
              Confirm
            </Button>
          </div>
        </div>
      )}

      {activePanel === "dismiss" && (
        <div
          className="mt-2 space-y-2 rounded-sm border border-border bg-muted/20 p-2"
          onClick={(e) => e.stopPropagation()}
        >
          <div className="micro-label text-muted-foreground">Reason (required)</div>
          <select
            value={dismissReason}
            onChange={(e) => setDismissReason(e.target.value as DismissReason)}
            className={SELECT_CLASS}
          >
            <option value="">Select a reason…</option>
            {DISMISS_REASONS.map((r) => (
              <option key={r.value} value={r.value}>
                {r.label}
              </option>
            ))}
          </select>
          <div className="flex justify-end gap-2">
            <Button size="sm" variant="ghost" onClick={() => setActivePanel(null)}>
              Cancel
            </Button>
            <Button size="sm" disabled={verdictBusy || !dismissReason} onClick={confirmDismiss}>
              Confirm dismiss
            </Button>
          </div>
        </div>
      )}
    </div>
  );
});
