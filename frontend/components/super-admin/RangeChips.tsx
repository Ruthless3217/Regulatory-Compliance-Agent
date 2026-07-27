"use client";
import { cn } from "@/lib/utils";

const RANGES = [7, 30, 90] as const;

/**
 * Date-range picker for the console. A small segmented control over the
 * `?days=` window every rollup endpoint accepts. Kept intentionally minimal
 * (7 / 30 / 90 days) to match the SQL rollups in audit-trail/02 §7.
 */
export function RangeChips({
  value,
  onChange,
}: {
  value: number;
  onChange: (days: number) => void;
}) {
  return (
    <div
      role="group"
      aria-label="Date range"
      className="inline-flex items-center gap-0.5 rounded-md border border-border bg-background p-0.5"
    >
      {RANGES.map((d) => {
        const active = value === d;
        return (
          <button
            key={d}
            type="button"
            onClick={() => onChange(d)}
            aria-pressed={active}
            className={cn(
              "rounded-sm px-2.5 py-1 text-xs font-medium transition-colors",
              active
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-muted hover:text-foreground"
            )}
          >
            {d}d
          </button>
        );
      })}
    </div>
  );
}
