import * as React from "react";
import { Sparkline } from "./sparkline";
import { cn } from "@/lib/utils";

interface Props {
  label: string;
  value: React.ReactNode;
  delta?: { value: number; positive?: boolean; suffix?: string };
  spark?: number[];
  icon?: React.ReactNode;
  sub?: React.ReactNode;
  tone?: "default" | "primary" | "success" | "danger";
  className?: string;
}

/**
 * Dense KPI card with optional sparkline + delta. Used in KPI strips on
 * Submissions inbox, Dashboard, and Submission workspace header.
 */
export function StatCard({ label, value, delta, spark, icon, sub, tone = "default", className }: Props) {
  const toneClass =
    tone === "primary"
      ? "border-primary/30 bg-primary-50/40"
      : tone === "success"
        ? "border-success/30 bg-success/5"
        : tone === "danger"
          ? "border-sev-critical/30 bg-sev-critical/5"
          : "border-border bg-background shadow-card";
  return (
    <div className={cn("flex items-start justify-between gap-3 rounded-lg border p-4 transition-shadow hover:shadow-md", toneClass, className)}>
      <div className="min-w-0">
        <div className="flex items-center gap-1.5">
          {icon && <span className="text-muted-foreground">{icon}</span>}
          <span className="micro-label">{label}</span>
        </div>
        <div className="mt-2 font-mono text-[26px] leading-none">{value}</div>
        <div className="mt-1.5 flex items-baseline gap-2">
          {delta && (
            <span
              className={cn(
                "font-mono text-[11px]",
                delta.positive ? "text-success" : "text-sev-critical"
              )}
            >
              {delta.positive ? "▲" : "▼"} {Math.abs(delta.value)}{delta.suffix ?? ""}
            </span>
          )}
          {sub && <span className="text-[11px] text-muted-foreground">{sub}</span>}
        </div>
      </div>
      {spark && spark.length > 0 && (
        <Sparkline values={spark} width={72} height={32} />
      )}
    </div>
  );
}
