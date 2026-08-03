"use client";
import * as React from "react";
import { FilterChipBar, type FilterKey, type SelectFilterDef, type SelectFilterOption } from "./FilterChipBar";
import { ViolationCard, ViolationRow } from "./ViolationCard";
import { severityOrder, normalizeSeverity, categoryLabel } from "@/lib/format";
import type { Violation } from "@/lib/types";

interface Props {
  violations: Violation[];
  selectedViolationId: string | null;
  setSelectedViolationId: (id: string | null) => void;
}

// Sentinels for the "no value" bucket of a facet — distinct from the "all"
// value so a violation lacking e.g. a section_title is still reachable as its
// own filter choice instead of silently disappearing from every specific pick.
const UNSPECIFIED = "__unspecified__";
const REVIEW_STATUS_OPEN = "__open__";

/** Distinct present values (trimmed, deduped, alpha-sorted) plus an
 * "Unspecified" bucket when at least one violation is missing the field. */
function distinctOptions(
  values: (string | null | undefined)[],
  unspecifiedLabel: string,
  labelFor: (v: string) => string = (v) => v
): SelectFilterOption[] {
  const seen = new Set<string>();
  let hasUnspecified = false;
  for (const raw of values) {
    const v = (raw ?? "").trim();
    if (!v) hasUnspecified = true;
    else seen.add(v);
  }
  const opts = Array.from(seen)
    .sort((a, b) => a.localeCompare(b))
    .map((v) => ({ value: v, label: labelFor(v) }));
  if (hasUnspecified) opts.push({ value: UNSPECIFIED, label: unspecifiedLabel });
  return opts;
}

/** review_status is null until a reviewer acts (see rule_feedback_service.py
 * — it only ever writes "actioned"), so null means "open" rather than
 * "unspecified". */
function reviewStatusOptions(values: (string | null | undefined)[]): SelectFilterOption[] {
  const seen = new Set<string>();
  let hasOpen = false;
  for (const raw of values) {
    const v = (raw ?? "").trim();
    if (!v) hasOpen = true;
    else seen.add(v);
  }
  const opts: SelectFilterOption[] = [];
  if (hasOpen) opts.push({ value: REVIEW_STATUS_OPEN, label: "Open" });
  for (const v of Array.from(seen).sort((a, b) => a.localeCompare(b))) {
    opts.push({ value: v, label: v.charAt(0).toUpperCase() + v.slice(1) });
  }
  return opts;
}

/** True if `raw` (a nullable facet value) satisfies a select's current
 * filter value, where `unspecifiedSentinel` stands in for "field is empty". */
function matchesFacet(raw: string | null | undefined, filterValue: string, unspecifiedSentinel: string): boolean {
  if (filterValue === "all") return true;
  const v = (raw ?? "").trim();
  return filterValue === unspecifiedSentinel ? !v : v === filterValue;
}

export function ViolationsPane({ violations, selectedViolationId, setSelectedViolationId }: Props) {
  const [filter, setFilter] = React.useState<FilterKey>("all");
  const [categoryFilter, setCategoryFilter] = React.useState("all");
  const [productFilter, setProductFilter] = React.useState("all");
  const [sectionFilter, setSectionFilter] = React.useState("all");
  const [reviewStatusFilter, setReviewStatusFilter] = React.useState("all");
  const [sourceFilter, setSourceFilter] = React.useState("all");
  const [showSuppressed, setShowSuppressed] = React.useState(false);
  const scrollRef = React.useRef<HTMLDivElement | null>(null);

  // Suppressed (sub-confidence-floor / structural) findings are kept out of the
  // score and the severity filter — surfaced in a separate "Needs review" lane.
  const active = React.useMemo(() => violations.filter((v) => !v.suppressed), [violations]);
  const suppressed = React.useMemo(() => violations.filter((v) => v.suppressed), [violations]);

  const sorted = React.useMemo(
    () => [...active].sort((a, b) => severityOrder(a.severity) - severityOrder(b.severity)),
    [active]
  );

  const counts = React.useMemo(() => {
    const base: Record<FilterKey, number> = { all: sorted.length, critical: 0, high: 0, medium: 0, low: 0 };
    for (const v of sorted) base[normalizeSeverity(v.severity)] += 1;
    return base;
  }, [sorted]);

  // Facet option lists are derived from the full active set (not the
  // currently-filtered one) so picking one filter never makes another
  // filter's own options disappear out from under the user.
  const categoryOptions = React.useMemo(
    () => distinctOptions(sorted.map((v) => v.category), "—", categoryLabel),
    [sorted]
  );
  const productOptions = React.useMemo(
    () => distinctOptions(sorted.map((v) => v.violation_metadata?.product_name), "No product tag"),
    [sorted]
  );
  const sectionOptions = React.useMemo(
    () => distinctOptions(sorted.map((v) => v.section_title), "No section"),
    [sorted]
  );
  const reviewStatusOpts = React.useMemo(
    () => reviewStatusOptions(sorted.map((v) => v.review_status)),
    [sorted]
  );
  // 0031 — who authored the finding. Normalized to "model" so a row from a
  // cached/older payload without `source` never lands in an Unspecified bucket.
  const sourceOptions = React.useMemo(
    () =>
      distinctOptions(
        sorted.map((v) => v.source ?? "model"),
        "—",
        (v) => (v === "reviewer" ? "Reviewer-added" : "Model")
      ),
    [sorted]
  );

  const filtered = React.useMemo(
    () =>
      sorted.filter(
        (v) =>
          (filter === "all" || normalizeSeverity(v.severity) === filter) &&
          (categoryFilter === "all" || v.category === categoryFilter) &&
          matchesFacet(v.violation_metadata?.product_name, productFilter, UNSPECIFIED) &&
          matchesFacet(v.section_title, sectionFilter, UNSPECIFIED) &&
          matchesFacet(v.review_status, reviewStatusFilter, REVIEW_STATUS_OPEN) &&
          matchesFacet(v.source ?? "model", sourceFilter, UNSPECIFIED)
      ),
    [filter, categoryFilter, productFilter, sectionFilter, reviewStatusFilter, sourceFilter, sorted]
  );

  const selectFilters: SelectFilterDef[] = [
    { key: "category", label: "Category", value: categoryFilter, options: categoryOptions, onChange: setCategoryFilter },
    { key: "product", label: "Product", value: productFilter, options: productOptions, onChange: setProductFilter },
    { key: "section", label: "Section", value: sectionFilter, options: sectionOptions, onChange: setSectionFilter },
    { key: "review-status", label: "Review status", value: reviewStatusFilter, options: reviewStatusOpts, onChange: setReviewStatusFilter },
    { key: "source", label: "Source", value: sourceFilter, options: sourceOptions, onChange: setSourceFilter },
  ];

  // The detail card lives at the top of this scroller, so selecting anything —
  // a row here, a highlight in the document — means scrolling back up to it.
  React.useEffect(() => {
    if (selectedViolationId) scrollRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  }, [selectedViolationId]);

  // Resolved against every violation, not just `filtered`: a finding selected
  // from the document (or a suppressed one) must still open in full even when
  // the current filter hides its row.
  const selectedViolation = React.useMemo(
    () => violations.find((v) => v.id === selectedViolationId) ?? null,
    [violations, selectedViolationId]
  );

  // Step through what is actually on screen, not the unfiltered set — landing
  // on a card the current filter hides would look like a dead button.
  const cursor = filtered.findIndex((v) => v.id === selectedViolationId);
  const step = (delta: number) => {
    if (filtered.length === 0) return;
    // No selection yet: Next opens the first card, Previous the last.
    const next = cursor === -1
      ? (delta > 0 ? 0 : filtered.length - 1)
      : cursor + delta;
    if (next < 0 || next >= filtered.length) return;
    setSelectedViolationId(filtered[next].id);
  };
  // review_status is null until a reviewer acts (rule_feedback_service only
  // ever writes "actioned"), so a non-null value means reviewed.
  const reviewedCount = filtered.filter((v) => (v.review_status ?? "").trim() !== "").length;

  // The dense list is everything except the one already open in full above it.
  const others = filtered.filter((v) => v.id !== selectedViolationId);

  return (
    <div className="flex h-full min-h-0 min-w-0 flex-col border-l border-border bg-background">
      <FilterChipBar counts={counts} value={filter} onChange={setFilter} selectFilters={selectFilters} />
      <div className="flex items-center justify-between gap-2 border-b border-border px-3 py-2">
        <span className="min-w-0 text-[11px] text-muted-foreground">
          {filtered.length === 0
            ? "No findings"
            : cursor === -1
              ? `None selected · ${filtered.length} findings · ${reviewedCount} reviewed`
              : `${cursor + 1} of ${filtered.length} · ${reviewedCount} reviewed`}
        </span>
        <div className="flex shrink-0 items-center gap-1">
          <button
            type="button"
            className="rounded-sm border border-border px-2 py-1 text-[11px] disabled:opacity-40"
            disabled={filtered.length === 0 || cursor === 0}
            onClick={() => step(-1)}
          >
            Previous
          </button>
          <button
            type="button"
            className="rounded-sm border border-border px-2 py-1 text-[11px] disabled:opacity-40"
            disabled={filtered.length === 0 || cursor === filtered.length - 1}
            onClick={() => step(1)}
          >
            Next
          </button>
        </div>
      </div>
      {/* One finding in full at the top, every other one as a dense line. The
          detail card is the only place a finding's evidence, verdict actions
          and rewrite live — the rows exist to get you to it. */}
      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto">
        {selectedViolation ? (
          <div className="p-3">
            <ViolationCard
              index={cursor === -1 ? undefined : cursor}
              violation={selectedViolation}
              selected
              onSelect={() => setSelectedViolationId(selectedViolation.id)}
            />
          </div>
        ) : (
          <p className="border-b border-border px-3 py-3 text-xs text-muted-foreground">
            No finding selected. Pick one below to see its rule, the regulator&rsquo;s wording and the
            suggested replacement.
          </p>
        )}

        {filtered.length === 0 ? (
          <div className="p-8 text-center text-sm text-muted-foreground">
            No violations in this filter.
          </div>
        ) : (
          others.map((v) => (
            <ViolationRow key={v.id} violation={v} onSelect={() => setSelectedViolationId(v.id)} />
          ))
        )}

        {suppressed.length > 0 && (
          <div className="border-t border-dashed border-border">
            <button
              type="button"
              onClick={() => setShowSuppressed((s) => !s)}
              className="flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-xs font-medium text-muted-foreground hover:text-foreground"
            >
              <span>
                Needs review ({suppressed.length}) — low-confidence or structural; not counted in the score
              </span>
              <span className="font-mono">{showSuppressed ? "−" : "+"}</span>
            </button>
            {showSuppressed &&
              suppressed
                .filter((v) => v.id !== selectedViolationId)
                .map((v) => (
                  <ViolationRow key={v.id} violation={v} onSelect={() => setSelectedViolationId(v.id)} />
                ))}
          </div>
        )}
      </div>
    </div>
  );
}
