"use client";
import * as React from "react";
import { cn } from "@/lib/utils";

type Key = "all" | "critical" | "high" | "medium" | "low";

/** One dynamic single-select facet (Category/Product/Section/Review-status).
 * `options` excludes the "All" sentinel — the bar prepends it. Values/labels
 * are computed by the caller from whatever's actually present in the current
 * violation set, so the list never offers a choice that would empty itself. */
export interface SelectFilterOption {
  value: string;
  label: string;
}
export interface SelectFilterDef {
  key: string;
  label: string;
  value: string;
  options: SelectFilterOption[];
  onChange: (value: string) => void;
}

interface Props {
  counts: Record<Key, number>;
  value: Key;
  onChange: (k: Key) => void;
  selectFilters?: SelectFilterDef[];
}

const ORDER: { key: Key; label: string; dotClass?: string }[] = [
  { key: "all", label: "All" },
  { key: "critical", label: "Critical", dotClass: "bg-sev-critical" },
  { key: "high", label: "High", dotClass: "bg-sev-high" },
  { key: "medium", label: "Medium", dotClass: "bg-sev-medium" },
  { key: "low", label: "Low", dotClass: "bg-sev-low" },
];

// w-full/min-w-0: a <select> is intrinsically as wide as its widest <option>
// (long section titles, "All review status"), which otherwise blows out the
// grid track and makes the row wrap raggedly in the ~400px pane.
const SELECT_CLASS =
  "h-[26px] w-full min-w-0 rounded-sm border border-border bg-background px-2 text-xs text-muted-foreground " +
  "transition-colors hover:border-foreground hover:text-foreground " +
  "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary";

const CHIP_CLASS =
  "inline-flex items-center gap-1.5 rounded-sm border px-2 py-1 text-xs transition-colors";

export function FilterChipBar({ counts, value, onChange, selectFilters }: Props) {
  // Secondary facets collapse by default — five selects cost three rows in a
  // ~370px pane. The count keeps a hidden active filter from being invisible.
  const [showSelects, setShowSelects] = React.useState(false);
  const activeSelects = (selectFilters ?? []).filter((f) => f.value !== "all");

  return (
    <div className="flex flex-wrap items-center gap-1.5 border-b border-border bg-background px-3 py-2">
      {ORDER.map((c) => {
        const active = c.key === value;
        const n = counts[c.key] ?? 0;
        return (
          <button
            key={c.key}
            type="button"
            onClick={() => onChange(c.key)}
            className={cn(
              CHIP_CLASS,
              active
                ? "border-foreground bg-foreground text-background"
                : "border-border bg-background text-muted-foreground hover:border-foreground hover:text-foreground"
            )}
          >
            {c.dotClass && (
              <span className={cn("inline-block h-1.5 w-1.5 rounded-full", c.dotClass)} />
            )}
            <span>{c.label}</span>
            <span className={cn("font-mono", active ? "opacity-90" : "text-muted-foreground")}>
              {n}
            </span>
          </button>
        );
      })}

      {selectFilters && selectFilters.length > 0 && (
        <>
          <button
            type="button"
            onClick={() => setShowSelects((s) => !s)}
            aria-expanded={showSelects}
            title={
              activeSelects.length > 0
                ? `Active: ${activeSelects.map((f) => f.label).join(", ")}`
                : "Category, product, section, review status, source"
            }
            className={cn(
              CHIP_CLASS,
              activeSelects.length > 0
                ? "border-foreground bg-primary-50 text-foreground"
                : "border-border bg-background text-muted-foreground hover:border-foreground hover:text-foreground"
            )}
          >
            <span>Filters</span>
            {activeSelects.length > 0 && (
              <span className="font-mono">{activeSelects.length} active</span>
            )}
            <span aria-hidden className="font-mono">{showSelects ? "−" : "+"}</span>
          </button>

          {/* w-full breaks the selects onto their own line under the chips; auto-fit
              fills as many even columns as fit (2 in the 400px pane) instead of
              packing content-sized selects greedily. */}
          {showSelects && (
            <div className="grid w-full grid-cols-[repeat(auto-fit,minmax(8rem,1fr))] gap-1.5">
              {selectFilters.map((f) => (
                <select
                  key={f.key}
                  value={f.value}
                  onChange={(e) => f.onChange(e.target.value)}
                  className={cn(SELECT_CLASS, f.value !== "all" && "border-foreground text-foreground")}
                  aria-label={f.label}
                >
                  <option value="all">All {f.label.toLowerCase()}</option>
                  {f.options.map((o) => (
                    <option key={o.value} value={o.value}>
                      {o.label}
                    </option>
                  ))}
                </select>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

export type { Key as FilterKey };
