"use client";
import * as React from "react";
import { PieChart, Pie, Cell, ResponsiveContainer, Tooltip } from "recharts";
import { severityColor, bucketSeverityRows } from "@/lib/format";

interface RowIn {
  severity: string;
  count: number;
}

interface Props {
  rows: RowIn[];
}

const ORDER = ["critical", "high", "medium", "low"] as const;

export function SeverityDonut({ rows }: Props) {
  const counts = bucketSeverityRows(rows);
  const data = ORDER.map((s) => ({ name: s, value: counts[s] })).filter((d) => d.value > 0);
  const total = data.reduce((a, d) => a + d.value, 0);

  return (
    <div className="rounded-lg border border-border bg-background p-6 shadow-card">
      <h3 className="text-base font-semibold tracking-tight">Severity distribution</h3>
      <p className="mt-1 text-xs text-muted-foreground">Violation counts by severity</p>

      {total === 0 ? (
        <div className="mt-4 flex h-56 items-center justify-center text-sm text-muted-foreground">
          No violations to plot
        </div>
      ) : (
        <div className="mt-4 flex flex-col items-center gap-6 sm:flex-row">
          <div className="relative h-56 w-56 shrink-0">
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={data}
                  dataKey="value"
                  nameKey="name"
                  innerRadius={62}
                  outerRadius={92}
                  paddingAngle={2}
                  stroke="hsl(var(--background))"
                  strokeWidth={2}
                >
                  {data.map((d) => (
                    <Cell key={d.name} fill={severityColor(d.name)} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: "hsl(var(--background))",
                    border: "1px solid hsl(var(--border))",
                    borderRadius: 8,
                    fontSize: 12,
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
            <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
              <span className="font-mono text-2xl leading-none">{total}</span>
              <span className="mt-1 text-[10px] uppercase tracking-wide text-muted-foreground">
                violations
              </span>
            </div>
          </div>

          <ul className="flex-1 space-y-2.5">
            {data.map((d) => {
              const pct = Math.round((d.value / total) * 100);
              return (
                <li key={d.name} className="flex items-center gap-3">
                  <span
                    className="h-2.5 w-2.5 shrink-0 rounded-sm"
                    style={{ background: severityColor(d.name) }}
                  />
                  <span className="flex-1 text-sm capitalize">{d.name}</span>
                  <span className="font-mono text-sm">{d.value}</span>
                  <span className="w-10 text-right font-mono text-xs text-muted-foreground">{pct}%</span>
                </li>
              );
            })}
          </ul>
        </div>
      )}
    </div>
  );
}
