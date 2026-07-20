"use client";

import * as React from "react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { StatusPill } from "@/components/ui/status-pill";
import type { Category, Severity } from "@/lib/types";

export interface FilterState {
  severities: string[];
  categories: string[];
  tiers: string[];
}

export const EMPTY_FILTER_STATE: FilterState = { severities: [], categories: [], tiers: [] };

const SEVERITY_OPTIONS: Severity[] = ["critical", "high", "medium", "low"];
const CATEGORY_OPTIONS: Category[] = ["irdai", "sebi", "regulatory", "brand", "seo"];
const TIER_OPTIONS: { value: string; label: string }[] = [
  { value: "precedent", label: "Precedent" },
  { value: "rule", label: "Rule" },
  { value: "novel", label: "Novel" },
  { value: "product_fact", label: "Product fact" },
];

function toggle(list: string[], value: string): string[] {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

function Chip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "rounded-full border px-2.5 py-0.5 text-[11px] font-medium capitalize transition-colors",
        active
          ? "border-primary/30 bg-primary/10 text-primary"
          : "border-border text-muted-foreground hover:bg-accent"
      )}
    >
      {children}
    </button>
  );
}

function FilterRow({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div role="group" aria-label={label} className="flex flex-wrap items-center gap-1.5">
      <span className="micro-label w-16 shrink-0" aria-hidden="true">
        {label}
      </span>
      {children}
    </div>
  );
}

/** Severity / category / grounding-tier toggle chips driving `filterViolations`. */
export function FilterChipBar({
  value,
  onChange,
}: {
  value: FilterState;
  onChange: (next: FilterState) => void;
}) {
  const activeCount = value.severities.length + value.categories.length + value.tiers.length;

  return (
    <div className="space-y-2.5 border-b border-border p-4">
      <FilterRow label="Severity">
        {SEVERITY_OPTIONS.map((s) => {
          const active = value.severities.includes(s);
          return (
            <button
              key={s}
              type="button"
              aria-pressed={active}
              onClick={() => onChange({ ...value, severities: toggle(value.severities, s) })}
              className={cn("rounded-full transition-opacity", !active && "opacity-45 hover:opacity-80")}
            >
              <StatusPill severity={s}>{s}</StatusPill>
            </button>
          );
        })}
      </FilterRow>
      <FilterRow label="Category">
        {CATEGORY_OPTIONS.map((c) => (
          <Chip
            key={c}
            active={value.categories.includes(c)}
            onClick={() => onChange({ ...value, categories: toggle(value.categories, c) })}
          >
            {c}
          </Chip>
        ))}
      </FilterRow>
      <FilterRow label="Grounding">
        {TIER_OPTIONS.map((t) => (
          <Chip
            key={t.value}
            active={value.tiers.includes(t.value)}
            onClick={() => onChange({ ...value, tiers: toggle(value.tiers, t.value) })}
          >
            {t.label}
          </Chip>
        ))}
      </FilterRow>
      {activeCount > 0 && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          className="h-6 px-2 text-xs text-muted-foreground"
          onClick={() => onChange(EMPTY_FILTER_STATE)}
        >
          Clear filters ({activeCount})
        </Button>
      )}
    </div>
  );
}
