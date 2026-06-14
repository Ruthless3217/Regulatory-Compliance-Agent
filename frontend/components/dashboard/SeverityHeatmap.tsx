"use client";
import * as React from "react";
import { bucketSeverityRows } from "@/lib/format";

interface RowIn {
  severity: string;
  count: number;
}

interface Props {
  rows: RowIn[];
}

const ORDER = ["critical", "high", "medium", "low"] as const;

export function SeverityHeatmap({ rows }: Props) {
  const counts = bucketSeverityRows(rows);
  const max = Math.max(1, ...ORDER.map((s) => counts[s]));
  const get = (s: (typeof ORDER)[number]) => counts[s];

  return (
    <div className="rounded-lg border border-border bg-background p-6 shadow-card">
      <h3 className="text-base font-semibold tracking-tight">Severity distribution</h3>
      <p className="mt-1 text-xs text-muted-foreground">Violation counts by severity</p>
      <div className="mt-4 space-y-3">
        {ORDER.map((s) => {
          const c = get(s);
          const pct = Math.round((c / max) * 100);
          return (
            <div key={s}>
              <div className="mb-1 flex items-center justify-between text-xs">
                <span className="capitalize">{s}</span>
                <span className="font-mono text-muted-foreground">{c}</span>
              </div>
              <div className="h-2 w-full rounded-full bg-muted">
                <div
                  className="h-2 rounded-full"
                  style={{
                    width: `${pct}%`,
                    background:
                      s === "critical"
                        ? "hsl(var(--sev-critical))"
                        : s === "high"
                          ? "hsl(var(--sev-high))"
                          : s === "medium"
                            ? "hsl(var(--sev-medium))"
                            : "hsl(var(--sev-low))",
                  }}
                />
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
